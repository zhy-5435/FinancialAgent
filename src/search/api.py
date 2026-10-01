"""L2 检索服务：FastAPI REST 接口
将 milvus_store.search() 封装为 POST/GET /search，出参含切片内容与溯源五要素。

启动方式（项目根目录执行，独占 MilvusLite 锁）：
    python -m uvicorn src.search.api:app --host 127.0.0.1 --port 8000
"""
from fastapi import FastAPI, HTTPException, Query

from src.data.config import MILVUS_COLLECTION, TOP_K
from src.data.milvus_client import milvus_store
from src.search.schemas import MAX_QUERY_LEN, MAX_TOP_K, SearchRequest, SearchResponse

app = FastAPI(
    title="L1 金融知识库检索服务",
    description=(
        "L2 检索服务层：基于 SQLite 权威主库 + Milvus 向量索引的语义检索 REST 接口，"
        "结果天然过滤非有效状态与生效期外切片，出参含溯源五要素供 Agent 强制溯源引用。"
    ),
    version="1.0.0",
)


@app.get("/health", summary="健康检查")
def health():
    """探活接口，Agent 可用于服务可用性探测"""
    return {"status": "ok", "collection": MILVUS_COLLECTION}


@app.post("/search", response_model=SearchResponse, summary="知识切片语义检索")
def search(req: SearchRequest):
    """
    将 query 向量化后在 Milvus 中做 COSINE 语义检索（过滤 status=valid 且文档生效期内），
    命中 knowledge_id 后回 SQLite 补全切片详情，返回切片内容与溯源信息。
    """
    try:
        results = milvus_store.search(req.query, top_k=req.top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检索服务内部错误：{e}")

    return SearchResponse(
        query=req.query,
        top_k=req.top_k,
        total=len(results),
        results=results,
    )


@app.get("/search", response_model=SearchResponse, summary="知识切片语义检索（GET 形式）")
def search_by_get(
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LEN, description="用户查询文本"),
    top_k: int = Query(TOP_K, ge=1, le=MAX_TOP_K, description=f"返回数量上限（1-{MAX_TOP_K}）"),
):
    """与 POST /search 等价的 GET 形式，便于调试与简单调用方集成"""
    try:
        results = milvus_store.search(query, top_k=top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检索服务内部错误：{e}")

    return SearchResponse(
        query=query,
        top_k=top_k,
        total=len(results),
        results=results,
    )


if __name__ == "__main__":
    import uvicorn

    from src.search.config import API_HOST, API_PORT

    uvicorn.run(app, host=API_HOST, port=API_PORT)
