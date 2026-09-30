"""FastAPI 轻量检索服务（L2 检索服务层）
将 milvus_store.search() 封装为 REST 接口，入参 query + top_k，
出参切片内容与溯源五要素，供上层 Agent 稳定调用。

启动方式（项目根目录执行）：
    uvicorn src.api:app --host 0.0.0.0 --port 8000
    或
    python -m src.api
"""
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.config import MILVUS_COLLECTION, TOP_K
from src.milvus_client import milvus_store

# top_k 上限保护：防止 Agent 侧异常参数拖垮检索
MAX_TOP_K = 50
MAX_QUERY_LEN = 500


# ---------- 请求 / 响应模型 ----------

class SearchRequest(BaseModel):
    """POST /search 请求体"""
    query: str = Field(
        ...,
        min_length=1,
        max_length=MAX_QUERY_LEN,
        description="用户查询文本",
        examples=["理财产品申购费率"],
    )
    top_k: int = Field(
        TOP_K,
        ge=1,
        le=MAX_TOP_K,
        description=f"返回的切片数量上限（1-{MAX_TOP_K}），默认 {TOP_K}",
    )


class SearchHit(BaseModel):
    """单条命中结果：切片内容 + 溯源信息"""
    knowledge_id: str = Field(..., description="切片唯一ID，如 L1-0001")
    doc_version_id: str = Field(..., description="文档版本ID，如 REG-003:V2020")
    chunk_type: str = Field(..., description="切片类型（clause/fee/risk 等十类）")
    content: str = Field(..., description="检索用改写内容，供模型阅读")
    original_text: str = Field(..., description="原文逐字摘录，人工核验依据")
    clause_position: str | None = Field(None, description="条款位置，产品说明书可能为 NULL")
    heading_path: str | None = Field(None, description="标题路径")
    keywords: list[str] = Field(..., description="切片关键词")
    doc_name: str = Field(..., description="文档名称")
    doc_type: str | None = Field(None, description="文档类型（监管规则/内部制度/产品说明书）")
    version: str | None = Field(None, description="文档版本号")
    publish_dept: str | None = Field(None, description="发布部门")
    effective_date: str | None = Field(None, description="文档生效日期")
    similarity: float = Field(..., description="余弦相似度，越大越相关")


class SearchResponse(BaseModel):
    """检索响应"""
    query: str = Field(..., description="回显查询文本")
    top_k: int = Field(..., description="本次请求的返回数量上限")
    total: int = Field(..., description="实际命中切片数")
    results: list[SearchHit] = Field(..., description="命中结果，按相似度降序")


# ---------- 应用实例 ----------

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
    return {
        "status": "ok",
        "collection": MILVUS_COLLECTION,
    }


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

    uvicorn.run(app, host="0.0.0.0", port=8000)
