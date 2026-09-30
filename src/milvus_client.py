import json
import time

import pandas as pd
from pymilvus import MilvusClient

from src.config import MILVUS_DB_PATH, MILVUS_COLLECTION, VECTOR_DIM, TOP_K
from src.embedding import embedding_service
from src.sqlite_client import sqlite_client


def date_str_to_ts(date_str: str) -> int:
    """日期字符串转时间戳，用于检索过滤"""
    return int(pd.to_datetime(date_str).timestamp())


class MilvusStore:
    def __init__(self):
        self.client = MilvusClient(str(MILVUS_DB_PATH))
        self.collection_name = MILVUS_COLLECTION
        self._init_collection()

    def _init_collection(self):
        """初始化集合，幂等操作
        注：knowledge_id 等业务字段为动态字段（不进 schema，随行存储），
        集合静态结构仅 id/vector；旧数据行由 sync_from_sqlite 的全删重插机制清理
        """
        if not self.client.has_collection(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                dimension=VECTOR_DIM,
                metric_type="COSINE",  # 显式指定余弦相似度，返回值越大越相似
                auto_id=False,
                description="L1核心权威知识库向量索引"
            )
            print(f"✅ Milvus集合 {self.collection_name} 创建完成")
        else:
            print(f"✅ Milvus集合 {self.collection_name} 已存在")

    def sync_from_sqlite(self):
        """
        全量同步SQLite有效切片到Milvus
        逻辑：先清空集合全部数据，再重新插入，保证双库强一致
        （生效/失效时间取自切片所属 doc_version，主键按行号自增，业务键为 knowledge_id）
        """
        df = sqlite_client.get_valid_knowledge_with_meta()
        if df.empty:
            print("⚠️ SQLite中无有效知识切片，同步终止")
            return

        self.client.load_collection(self.collection_name)

        # 先清空现有数据再插入，否则重复构建会累积重复向量
        self.client.delete(collection_name=self.collection_name, filter="id >= 0")

        # 批量生成向量
        contents = df["content"].tolist()
        vectors = embedding_service.encode_doc_batch(contents)

        # 构造插入数据
        data = []
        for idx, (_, row) in enumerate(df.iterrows()):
            data.append({
                "id": idx + 1,
                "vector": vectors[idx],
                "knowledge_id": row["knowledge_id"],
                "doc_version_id": row["doc_version_id"],
                "effective_ts": date_str_to_ts(row["effective_date"]),
                "expire_ts": date_str_to_ts(row["expire_date"]),
                "status": row["status"]
            })

        # 批量插入
        self.client.insert(
            collection_name=self.collection_name,
            data=data
        )
        print(f"✅ Milvus同步完成，共插入 {len(data)} 条有效向量")

    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        """
        带过滤的向量检索
        过滤规则：仅返回切片状态为valid、且所属文档在生效时间范围内的知识点
        """
        # 搜索前先加载集合到内存
        self.client.load_collection(self.collection_name)

        current_ts = int(time.time())
        query_vector = embedding_service.encode_query(query)

        filter_expr = (
            f'status == "valid" '
            f'and effective_ts <= {current_ts} '
            f'and expire_ts >= {current_ts}'
        )

        results = self.client.search(
            collection_name=self.collection_name,
            data=[query_vector],
            filter=filter_expr,
            limit=top_k,
            output_fields=["knowledge_id"]
        )

        hits = results[0]
        if not hits:
            return []

        # 关联SQLite获取完整详情
        kid_list = [hit["entity"]["knowledge_id"] for hit in hits]
        df_detail = sqlite_client.get_knowledge_detail_by_ids(kid_list)

        # 组装结果，按相似度降序重排，确保Top1是最相关的
        result = []
        for hit in hits:
            kid = hit["entity"]["knowledge_id"]
            matched = df_detail[df_detail["knowledge_id"] == kid]
            if matched.empty:
                continue
            detail = matched.iloc[0]
            # COSINE 度量下 Milvus 返回的 distance 即余弦相似度，范围[-1,1]，越大越相似
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
                "similarity": round(float(hit["distance"]), 4)
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
