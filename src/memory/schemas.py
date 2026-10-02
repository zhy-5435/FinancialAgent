"""会话记忆层数据模型：优先级标签、预算档位、存储消息、压缩记录、上下文视图

约定：
    - PriorityTag 的 eviction_rank 越小越先被淘汰（锁存实体=0 永不淘汰，冗余=4 最先淘汰）；
    - StoredMessage 为持久化层读写单元，与 conversation_message 表列一一对应；
    - ContextBundle 为上下文引擎产出、交推理层直接消费的消息视图。
"""
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field

# 消息角色：与 LLM 对话消息一致，外加摘要注入的 system-summary 与工具结果 tool
MessageRole = Literal["user", "assistant", "tool", "system-summary"]

# 意图与作答类型枚举：与 src/agent/schemas.py、intent.py 保持同一组取值
IntentName = Literal["kb_qa", "chitchat", "news_search", "quote_query"]
# 作答类型：含 agentic（多工具自主循环）与 confirm（行情灰色区间事前确认），与 L3 graph finalize 产出对齐
AnswerType = Literal["generated", "refused", "chatted", "searched", "quoted", "agentic", "confirm"]

# 压缩作用域与被覆盖状态
CompressionScope = Literal["local", "global"]
CompressionState = Literal["active", "summarized", "replaced"]


class PriorityTag(IntEnum):
    """消息优先级分级：数值越小越重要、越晚淘汰（参考设计：锁存实体 > 核心业务 > 工具结果 > 普通对话 > 冗余）"""

    LATCHED_ENTITY = 0   # 锁存实体：标的代码、产品名等关键业务实体，强制常驻不裁剪
    CORE_BUSINESS = 1    # 核心业务：实质性用户提问与基于检索/工具的作答
    TOOL_RESULT = 2      # 工具结果：检索命中、网搜素材、行情快照（结构化精简保留）
    NORMAL = 3           # 普通对话：闲聊寒暄、无实质业务内容的往来
    REDUNDANT = 4        # 冗余：过程日志、重试、低相似度被拒条目，最先清理

    @property
    def tag_name(self) -> str:
        return self.name.lower()

    @classmethod
    def from_name(cls, name: str) -> "PriorityTag":
        return cls[name.upper()]


class StoredMessage(BaseModel):
    """持久化消息单元（对应 conversation_message 表）"""

    message_id: str = Field(..., description="消息唯一 ID，供追溯与压缩覆盖登记")
    session_id: str = Field(..., description="会话 ID")
    seq: int = Field(..., description="会话内单调递增序号，排序与裁剪依据")
    role: MessageRole = Field(..., description="消息角色")
    content: str = Field(..., description="脱敏后正文")
    intent: IntentName | None = Field(None, description="本轮识别意图（user/assistant 消息）")
    answer_type: AnswerType | None = Field(None, description="本轮作答类型（assistant 消息）")
    priority_tag: PriorityTag = Field(PriorityTag.NORMAL, description="优先级分级")
    token_est: int = Field(0, description="入库即估算的 token 数，供预算快速汇总")
    tool_name: str | None = Field(None, description="tool 消息的工具名")
    tool_payload: str | None = Field(None, description="tool 消息的结构化精简负载（JSON 字符串）")
    compression_state: CompressionState = Field("active", description="是否已被摘要覆盖")
    created_at: str | None = Field(None, description="入库时间")

    @property
    def is_context_bearing(self) -> bool:
        """是否会作为历史注入推理上下文（tool / system-summary / 已被摘要覆盖的消息不直接注入原文）"""
        return self.compression_state == "active" and self.role in ("user", "assistant")


class CompressionRecord(BaseModel):
    """压缩元数据日志（对应 compression_log 表）：每次局部/全局摘要登记一条，版本递增，可回溯被替换消息"""

    record_id: str = Field(..., description="压缩记录唯一 ID")
    session_id: str = Field(..., description="会话 ID")
    scope: CompressionScope = Field(..., description="作用域：局部 / 全局")
    version: int = Field(..., description="会话内递增版本号")
    covered_message_ids: list[str] = Field(default_factory=list, description="被本摘要替换的消息 ID 列表")
    summary_text: str = Field(..., description="摘要正文")
    summary_token_est: int = Field(0, description="摘要 token 估算")
    key_entities: list[str] = Field(default_factory=list, description="强制存活的关键实体清单")
    created_at: str | None = Field(None, description="生成时间")


class SessionSummary(BaseModel):
    """会话列表项：供历史恢复接口返回"""

    session_id: str
    title: str = Field("", description="会话标题（首条用户提问）")
    updated_at: str | None = Field(None, description="最近一条消息时间")
    message_count: int = Field(0, description="消息总数")


# 反馈标注类别：useful=有用 / useless=无用 / correction=内容纠错（HITL 反馈闭环）
FeedbackCategory = Literal["useful", "useless", "correction"]


class FeedbackRecord(BaseModel):
    """回答反馈标注（对应 answer_feedback 表）：拒答/低置信案例回流，判定「补文档」还是「调阈值」。

    纯增量的离线标注数据，不参与任何运行时决策；answer_type/intent/question 在落库时从被反馈的
    助手消息（conversation_message）服务端回填，不采信前端上送，避免污染校准数据集。
    """

    feedback_id: str = Field(..., description="反馈记录唯一 ID")
    session_id: str = Field(..., description="会话 ID")
    message_id: str = Field(..., description="被反馈的助手回答消息 ID（conversation_message.message_id）")
    category: FeedbackCategory = Field(..., description="反馈类别")
    comment: str | None = Field(None, description="内容纠错说明（category=correction 时）")
    answer_type: str | None = Field(None, description="被反馈回答的作答类型快照（服务端回填）")
    intent: str | None = Field(None, description="被反馈回答的意图快照（服务端回填）")
    question: str | None = Field(None, description="对应的用户提问快照（服务端回填，已脱敏）")
    created_at: str | None = Field(None, description="落库时间")


class TokenReport(BaseModel):
    """上下文预算报告：记录本次组装的用量与落档，供观测与测试断言"""

    system_tokens: int = 0
    history_tokens: int = 0
    fresh_tokens: int = 0
    total_tokens: int = 0
    budget_tokens: int = Field(0, description="可用预算 = CONTEXT_MAX_TOKENS - CONTEXT_OUTPUT_RESERVE")
    usage_ratio: float = Field(0.0, description="total / budget")
    tier: str = Field("direct", description="落档：direct/warning/global/trim")
    summarized: bool = Field(False, description="是否触发了摘要")
    trimmed_count: int = Field(0, description="被裁剪的历史消息条数")


class ContextBundle(BaseModel):
    """上下文构建引擎产出：交推理层直接消费的精简消息视图"""

    session_id: str
    system_prompt: str = Field(..., description="固定系统提示词（永不压缩）")
    messages: list[dict] = Field(
        default_factory=list,
        description="拼装后的消息序列 [{role, content}]，含历史前缀 + 本轮用户问题",
    )
    tier: str = Field("direct", description="落档：direct/warning/global/trim")
    summary_ref: str | None = Field(None, description="若触发摘要，对应 compression_log.record_id")
    token_report: TokenReport = Field(default_factory=TokenReport)
