"""L2 检索服务请求 / 响应模型"""
from pydantic import BaseModel, Field

from src.data.config import TOP_K

MAX_TOP_K = 50
MAX_QUERY_LEN = 500


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
    knowledge_id: str = Field(..., description="切片唯一 ID，如 L1-0001")
    doc_version_id: str = Field(..., description="文档版本 ID，如 REG-003:V2020")
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
    keyword_score: float = Field(..., description="关键词得分（BM25 按当前查询最优值归一化到 [0,1]）")
    score: float = Field(..., description="综合得分 = 相似度×0.7 + 关键词得分×0.3，降序排序依据")


class SearchResponse(BaseModel):
    """检索响应"""
    query: str = Field(..., description="回显查询文本")
    top_k: int = Field(..., description="本次请求的返回数量上限")
    total: int = Field(..., description="实际命中切片数")
    results: list[SearchHit] = Field(..., description="命中结果，按综合得分降序")
