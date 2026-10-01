"""L3 Agent HTTP 服务请求 / 响应模型"""
from pydantic import BaseModel, Field
from typing import Literal

MAX_QUESTION_LEN = 500

# 意图枚举：与 src/agent/intent.py 的 IntentName 保持同一组取值
IntentName = Literal["kb_qa", "chitchat", "news_search", "quote_query"]


class ChatRequest(BaseModel):
    """POST /chat 请求体"""
    question: str = Field(
        ...,
        min_length=1,
        max_length=MAX_QUESTION_LEN,
        description="用户问题",
        examples=["理财产品申购费率是多少"],
    )
    session_id: str | None = Field(
        None,
        max_length=64,
        description="会话 ID（预留字段：当前服务端无状态，缺省时生成并回传，供后续多轮会话复用）",
    )


class SourceHit(BaseModel):
    """单条溯源证据：作答依据的 knowledge_chunk 切片"""
    knowledge_id: str = Field(..., description="切片唯一 ID")
    doc_version_id: str = Field(..., description="文档版本 ID")
    chunk_type: str = Field(..., description="切片类型")
    content: str = Field(..., description="检索用改写内容")
    original_text: str = Field(..., description="原文逐字摘录")
    clause_position: str | None = Field(None, description="条款位置")
    heading_path: str | None = Field(None, description="标题路径")
    keywords: list[str] = Field(default_factory=list, description="切片关键词")
    doc_name: str = Field(..., description="文档名称")
    doc_type: str | None = Field(None, description="文档类型")
    version: str | None = Field(None, description="文档版本号")
    publish_dept: str | None = Field(None, description="发布部门")
    effective_date: str | None = Field(None, description="生效日期")
    similarity: float = Field(..., description="余弦相似度")
    keyword_score: float = Field(..., description="关键词得分（BM25 归一化到 [0,1]）")
    score: float = Field(..., description="综合得分 = 归一化向量相似度×0.8 + 归一化关键词得分×0.2")


class ChatResponse(BaseModel):
    """POST /chat 响应体"""
    session_id: str = Field(..., description="会话 ID（请求缺省时由服务端生成）")
    message_id: str = Field(..., description="本次回答唯一 ID，供后续反馈/追溯扩展")
    question: str = Field(..., description="回显用户问题")
    answer: str = Field(..., description="回答文本（带溯源）、闲聊回复、拒答或占位话术")
    intent: IntentName = Field(..., description="识别意图：kb_qa=知识库问答；chitchat=闲聊/常识；news_search=财经资讯（白名单网搜简报）；quote_query=实时行情（已接入新浪实时数据源）；低置信度回落 kb_qa")
    answer_type: str = Field(..., description="generated=基于有效切片作答；refused=无有效证据拒答；chatted=闲聊/常识对话；searched=财经简报（白名单网搜证据生成，无素材时为固定话术）；quoted=实时行情快照（表格化，含来源与免责，不经 LLM 转写）")
    sources: list[SourceHit] = Field(default_factory=list, description="作答依据切片列表，非 kb_qa 分支与拒答时为空")
    elapsed_ms: int = Field(..., description="本次问答耗时（毫秒）")
