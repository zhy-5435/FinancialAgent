"""双路混合检索编排：向量语义召回 + FTS5 关键词召回 → 去重补齐 → 加权排序

检索链路（金融场景「精准优先」）：
    用户提问
        ├─ 向量检索（Milvus Lite）  → 语义召回 Top CANDIDATE_TOP_K
        ├─ 关键词检索（SQLite FTS5）→ 精准召回 Top CANDIDATE_TOP_K
        └─ 两路结果去重 → 加权排序 → 输出 Top K

综合得分 = 归一化向量相似度 × VECTOR_WEIGHT + 归一化关键词得分 × KEYWORD_WEIGHT；
向量相似度（BGE 余弦常压缩在 0.5~0.75）与关键词得分（铺满 0~1）量纲不一致，
直接加权会放大关键词噪声、把向量路正确 Top1 挤掉，故两路得分先各自按当次查询
候选集做 min-max 归一化到 [0,1] 再加权，保证同量纲可比；
仅关键词路命中的切片按查询向量补算余弦相似度，保证两路得分口径一致；
FTS5 不可用时自动退化为纯向量检索，出参统一携带 similarity / keyword_score / score。
"""
from src.data.config import (
    CANDIDATE_TOP_K,
    KEYWORD_WEIGHT,
    RERANK_CANDIDATE_K,
    RERANK_ENABLED,
    TOP_K,
    VECTOR_WEIGHT,
)
from src.data.milvus_client import milvus_store
from src.data.reranker import reranker_service
from src.data.sqlite_client import sqlite_client


class HybridRetriever:
    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        """双路混合检索主入口，出参结构兼容原向量检索结果，附加综合得分

        排序链路：双路召回 → 去重补齐 → 归一化加权排序 → （可选）Reranker 精排 → 截断 Top K。
        """
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
            return self._rerank(query, results, top_k)

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

        # 3) 两路得分同量纲校准后加权排序：
        #    先对合并候选集的 similarity、keyword_score 各做 min-max 归一化到 [0,1]，
        #    再按 VECTOR_WEIGHT/KEYWORD_WEIGHT 加权，消除量纲差异导致的关键词噪声放大。
        sim_norm = self._minmax([h["similarity"] for h in merged])
        kw_norm = self._minmax([h["keyword_score"] for h in merged])
        for h in merged:
            h["score"] = round(
                sim_norm[h["similarity"]] * VECTOR_WEIGHT
                + kw_norm[h["keyword_score"]] * KEYWORD_WEIGHT,
                4,
            )
        # 综合得分并列时以原始向量相似度兜底（min-max 为保序线性变换，等价于按原始相似度破平）
        merged.sort(key=lambda x: (x["score"], x["similarity"]), reverse=True)
        return self._rerank(query, merged, top_k)

    def _rerank(self, query: str, ranked: list[dict], top_k: int) -> list[dict]:
        """对加权排序后的候选集做 Reranker 精排，再截断 Top K

        仅取排序前 RERANK_CANDIDATE_K 条送交叉编码器逐对打分，按精排得分重排；
        未开启或模型不可用时原序返回（不额外挂 rerank_score 字段），保证零副作用降级。
        """
        if not ranked or not RERANK_ENABLED or not reranker_service.available:
            return ranked[:top_k]
        pool = ranked[:RERANK_CANDIDATE_K]
        scores = reranker_service.scores(query, [h["content"] for h in pool])
        if not scores:
            return ranked[:top_k]
        for h, s in zip(pool, scores):
            h["rerank_score"] = round(s, 4)
        # 精排得分为主序，并列时回退加权综合得分兜底
        pool.sort(key=lambda x: (x["rerank_score"], x["score"]), reverse=True)
        return pool[:top_k]

    @staticmethod
    def _minmax(values: list[float]) -> dict[float, float]:
        """返回 {原始值: 归一化值}；全集同值或空集时归一化统一记 0（退化为另一路主导）。"""
        if not values:
            return {}
        lo, hi = min(values), max(values)
        span = hi - lo
        if span < 1e-9:
            return {v: 0.0 for v in values}
        return {v: (v - lo) / span for v in values}


# 全局实例
hybrid_retriever = HybridRetriever()
