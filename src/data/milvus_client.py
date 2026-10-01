"""Milvus Lite 向量索引：全量同步（幂等）/ 带生效期过滤的语义检索"""
import json
import time

import pandas as pd
from pymilvus import MilvusClient

from src.config import MILVUS_DB_PATH
from src.data.config import MILVUS_COLLECTION, TOP_K, VECTOR_DIM
from src.data.embedding import embedding_service
from src.data.sqlite_client import sqlite_client


def _date_str_to_ts(date_str: str) -> int:
    """日期字符串转 Unix 时间戳，用于检索过滤"""
    return int(pd.to_datetime(date_str).timestamp())


class MilvusStore:
    def __init__(self):
        self.client = MilvusClient(str(MILVUS_DB_PATH))
        self.collection_name = MILVUS_COLLECTION
        self._init_collection()

    def _init_collection(self):
        """初始化集合，幂等操作。
        静态 schema 仅含 id / vector；knowledge_id 等业务字段为动态字段（随行存储），
        旧版本遗留数据行由 sync_from_sqlite 的全删重插机制统一清理。
        """
        if not self.client.has_collection(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                dimension=VECTOR_DIM,
                metric_type="COSINE",  # 余弦相似度，返回值越大越相似
                auto_id=False,
                description="L1 核心权威知识库向量索引",
            )
            print(f"Milvus 集合 {self.collection_name} 创建完成")
        else:
            print(f"Milvus 集合 {self.collection_name} 已存在")

    def sync_from_sqlite(self):
        """全量同步 SQLite 有效切片到 Milvus：先清空集合再重新插入，保证双库强一致。"""
        df = sqlite_client.get_valid_knowledge_with_meta()
        if df.empty:
            print("SQLite 中无有效知识切片，同步终止")
            return

        self.client.load_collection(self.collection_name)
        # 清空现有向量，防止重复构建时累积旧数据
        self.client.delete(collection_name=self.collection_name, filter="id >= 0")

        contents = df["content"].tolist()
        vectors = embedding_service.encode_doc_batch(contents)

        data = []
        for idx, (_, row) in enumerate(df.iterrows()):
            data.append({
                "id": idx + 1,
                "vector": vectors[idx],
                "knowledge_id": row["knowledge_id"],
                "doc_version_id": row["doc_version_id"],
                "effective_ts": _date_str_to_ts(row["effective_date"]),
                "expire_ts": _date_str_to_ts(row["expire_date"]),
                "status": row["status"],
            })

        self.client.insert(collection_name=self.collection_name, data=data)
        print(f"Milvus 同步完成，共插入 {len(data)} 条有效向量")

    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        """带过滤的向量检索：仅返回 status=valid 且所属文档在生效时间范围内的切片。"""
        self.client.load_collection(self.collection_name)

        current_ts = int(time.time())
        query_vector = embedding_service.encode_query(query)
        filter_expr = (
            f'status == "valid" '
            f"and effective_ts <= {current_ts} "
            f"and expire_ts >= {current_ts}"
        )

        results = self.client.search(
            collection_name=self.collection_name,
            data=[query_vector],
            filter=filter_expr,
            limit=top_k,
            output_fields=["knowledge_id"],
        )

        hits = results[0]
        if not hits:
            return []

        kid_list = [hit["entity"]["knowledge_id"] for hit in hits]
        df_detail = sqlite_client.get_knowledge_detail_by_ids(kid_list)

        result = []
        for hit in hits:
            kid = hit["entity"]["knowledge_id"]
            matched = df_detail[df_detail["knowledge_id"] == kid]
            if matched.empty:
                continue
            detail = matched.iloc[0]
            # COSINE 度量下 Milvus 返回的 distance 即余弦相似度，范围 [-1,1]，越大越相似
            result.append({
                "knowledge_id": kid,
                "doc_version_id": detail["doc_version_id"],
                "chunk_type": detail["chunk_type"],
                "content": detail["content"],
                "original_text": detail["original_text"],
                "clause_position": None if pd.isna(detail["clause_position"]) else detail["clause_position"],
                "heading_path": detail["heading_path"],
                "keywords": self._parse_keywords(detail["keywords"]),
                "doc_name": detail["doc_name"],
                "doc_type": detail["doc_type"],
                "version": detail["version"],
                "publish_dept": detail["publish_dept"],
                "effective_date": detail["effective_date"],
                "similarity": round(float(hit["distance"]), 4),
            })

        result.sort(key=lambda x: x["similarity"], reverse=True)
        return result

    @staticmethod
    def _parse_keywords(raw) -> list:
        """keywords 在 SQLite 中为 JSON 字符串，解析为列表便于上层直接使用"""
        if not isinstance(raw, str) or not raw.strip():
            return []
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []


# 全局实例
milvus_store = MilvusStore()
