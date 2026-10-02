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
    rerank_score: float | None = Field(None, description="Reranker 精排得分（开启精排时返回，为最终排序依据；关闭/降级时为 null）")


class ChatResponse(BaseModel):
    """POST /chat 响应体"""
    session_id: str = Field(..., description="会话 ID（请求缺省时由服务端生成）")
    message_id: str = Field(..., description="本次助手回答在 memory 中的消息 ID（conversation_message.message_id），供 /feedback 反馈锚定与溯源；单轮无存档时回落服务端生成 ID")
    question: str = Field(..., description="回显用户问题")
    answer: str = Field(..., description="回答文本（带溯源）、闲聊回复、拒答或占位话术")
    intent: IntentName = Field(..., description="识别意图：kb_qa=知识库问答；chitchat=闲聊/常识；news_search=财经资讯（白名单网搜简报）；quote_query=实时行情（已接入新浪实时数据源）；Agent Loop 下由本轮实际调用的工具回溯派生")
    answer_type: str = Field(..., description="generated=基于有效切片作答；refused=无有效证据拒答；chatted=闲聊/常识对话；searched=财经简报（白名单网搜证据生成，无素材时为固定话术）；quoted=实时行情快照（表格化，含来源与免责，不经 LLM 转写）；agentic=多工具组合的自主循环作答；confirm=行情灰色区间，待用户从候选标的事前确认")
    sources: list[SourceHit] = Field(default_factory=list, description="作答依据切片列表，非 kb_qa 分支与拒答时为空")
    steps: list[dict] | None = Field(None, description="Agent Loop 过程轨迹（plan / tool_result 事件序列），供前端展示推理过程；缺省不下发")
    confirm: dict | None = Field(None, description="answer_type=confirm 时的行情确认载荷（question/guessed/candidates），其余为 null；缺省不下发")
    elapsed_ms: int = Field(..., description="本次问答耗时（毫秒）")


class FeedbackRequest(BaseModel):
    """POST /feedback 请求体：对某条助手回答的 HITL 反馈（纯增量标注，不参与运行时决策）"""
    session_id: str = Field(..., min_length=1, max_length=64, description="会话 ID")
    message_id: str = Field(..., min_length=1, max_length=64, description="被反馈的助手回答消息 ID（来自 /chat 的 message_id）")
    category: Literal["useful", "useless", "correction"] = Field(
        ..., description="反馈类别：useful=有用；useless=无用/答非所问；correction=内容纠错",
    )
    comment: str | None = Field(
        None, max_length=1000,
        description="内容纠错说明（category=correction 时填写应更正为什么）；入库前服务端做 PII 脱敏",
    )


class FeedbackResponse(BaseModel):
    """POST /feedback 响应体"""
    feedback_id: str = Field(..., description="反馈标注唯一 ID")
    recorded: bool = Field(True, description="是否成功落库")


class QuoteConfirmRequest(BaseModel):
    """POST /quote/confirm 请求体：用户在确认条点选候选标的后，按已知新浪取数键确定性取数"""
    session_id: str = Field(..., min_length=1, max_length=64, description="会话 ID")
    code: str = Field(
        ..., min_length=2, max_length=32,
        pattern=r"^(?:(?:sh|sz|bj)\d{6}|rt_hk(?:\d{5}|[A-Za-z]{2,8})|gb_[A-Za-z.]{1,12})$",
        description="候选标的的新浪取数键（confirm 事件 candidates[].code），仅接受白名单代码格式",
    )
    name: str | None = Field(None, max_length=80, description="候选标的显示名（用于回显与历史存档语境）")


class QuoteConfirmResponse(BaseModel):
    """POST /quote/confirm 响应体：与行情终答同形状（answer_type=quoted，数字逐字来自数据源）"""
    session_id: str = Field(..., description="会话 ID")
    message_id: str = Field(..., description="本次行情回答在 memory 中的消息 ID，供 /feedback 锚定")
    answer: str = Field(..., description="行情 Markdown（表格 + 来源 + 免责，不经 LLM 转写）")
    answer_type: str = Field("quoted", description="恒为 quoted")
    intent: IntentName = Field("quote_query", description="恒为 quote_query")
    sources: list[SourceHit] = Field(default_factory=list, description="行情分支无知识库切片，恒为空")
    elapsed_ms: int = Field(..., description="取数耗时（毫秒）")


class SessionItem(BaseModel):
    """GET /sessions 会话列表项（供前端侧栏历史恢复）"""
    session_id: str = Field(..., description="会话 ID")
    title: str = Field("", description="会话标题（首条用户提问）")
    updated_at: str | None = Field(None, description="最近一条消息时间")
    message_count: int = Field(0, description="消息总数")


class RestoredMessage(BaseModel):
    """历史恢复的单条可见消息（仅 user/assistant）"""
    message_id: str = Field(..., description="消息唯一 ID")
    role: str = Field(..., description="user / assistant")
    content: str = Field(..., description="消息正文（存档已做字段级脱敏）")
    intent: str | None = Field(None, description="本轮意图（user 消息）")
    answer_type: str | None = Field(None, description="作答类型（assistant 消息）")
    created_at: str | None = Field(None, description="入库时间")


class SessionMessagesResponse(BaseModel):
    """GET /sessions/{session_id}/messages 响应体"""
    session_id: str = Field(..., description="会话 ID")
    messages: list[RestoredMessage] = Field(default_factory=list, description="按时间升序的可见历史消息")
