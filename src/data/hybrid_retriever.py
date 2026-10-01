"""双路混合检索编排：向量语义召回 + FTS5 关键词召回 → 去重补齐 → 加权排序

检索链路（金融场景「精准优先」）：
    用户提问
        ├─ 向量检索（Milvus Lite）  → 语义召回 Top CANDIDATE_TOP_K
        ├─ 关键词检索（SQLite FTS5）→ 精准召回 Top CANDIDATE_TOP_K
        └─ 两路结果去重 → 加权排序 → 输出 Top K

综合得分 = 向量相似度 × VECTOR_WEIGHT + 归一化关键词得分 × KEYWORD_WEIGHT；
仅关键词路命中的切片按查询向量补算余弦相似度，保证两路得分口径一致；
FTS5 不可用时自动退化为纯向量检索，出参统一携带 similarity / keyword_score / score。
"""
from src.data.config import CANDIDATE_TOP_K, KEYWORD_WEIGHT, TOP_K, VECTOR_WEIGHT
from src.data.milvus_client import milvus_store
from src.data.sqlite_client import sqlite_client


class HybridRetriever:
    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        """双路混合检索主入口，出参结构兼容原向量检索结果，附加综合得分"""
        # 1) 双路召回
        vec_hits = milvus_store.search(query, top_k=CANDIDATE_TOP_K)
        kw_hits = sqlite_client.keyword_search(query, top_k=CANDIDATE_TOP_K)

        if not kw_hits:
            # 关键词路不可用（FTS5 缺失/无词元命中）时退化为纯向量检索，仍统一补齐得分字段
            results = [
                {**h, "keyword_score": 0.0, "score": round(h["similarity"] * VECTOR_WEIGHT, 4)}
                for h in vec_hits
            ]
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:top_k]

        # 2) 去重合并：向量路条目补关键词得分，仅关键词路命中的条目补算向量相似度
        kw_score_map = {h["knowledge_id"]: h["keyword_score"] for h in kw_hits}
        vec_ids = {h["knowledge_id"] for h in vec_hits}
        kw_only = [h for h in kw_hits if h["knowledge_id"] not in vec_ids]
        sim_map = (
            milvus_store.get_similarity_by_ids(query, [h["knowledge_id"] for h in kw_only])
            if kw_only else {}
        )

        merged = []
        for h in vec_hits:
            merged.append({**h, "keyword_score": kw_score_map.get(h["knowledge_id"], 0.0)})
        for h in kw_only:
            # coverage 仅为关键词路内部弱命中诊断字段，不透出到最终出参
            hit = {k: v for k, v in h.items() if k != "coverage"}
            hit["similarity"] = sim_map.get(h["knowledge_id"], 0.0)
            merged.append(hit)

        # 3) 加权排序：综合得分 = 向量相似度×0.7 + 归一化关键词得分×0.3，降序取 Top K
        for h in merged:
            h["score"] = round(
                h["similarity"] * VECTOR_WEIGHT + h["keyword_score"] * KEYWORD_WEIGHT, 4
            )
        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:top_k]


# 全局实例
hybrid_retriever = HybridRetriever()
