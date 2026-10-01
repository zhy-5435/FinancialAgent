"""工具调用预处理：丢弃重试/过程日志、把历史工具结果精简为结构化摘要形态

历史工具结果（检索切片 / 网搜素材 / 行情快照）原始 payload 体积大，直接进上下文会迅速吃满预算。
本模块将其压成紧凑渲染文本，仅保留高分要点条目；纯过程性/冗余条目丢弃不注入。
"""
import json

from src.memory.config import TOOL_RESULT_KEEP_HITS
from src.memory.schemas import PriorityTag, StoredMessage


def _render_retrieval(payload: list[dict]) -> str:
    """检索命中精简：按 score 降序保留前 N 条，每条呈现 文档/版本/条款 + 正文截断"""
    ranked = sorted(payload, key=lambda h: h.get("score", h.get("similarity", 0)), reverse=True)
    lines = []
    for h in ranked[:TOOL_RESULT_KEEP_HITS]:
        clause = h.get("clause_position") or "无条款号"
        snippet = (h.get("content") or "").strip().replace("\n", " ")
        if len(snippet) > 120:
            snippet = snippet[:120] + "…"
        lines.append(f"- {h.get('doc_name', '未知文档')}（{clause}）：{snippet}")
    return "\n".join(lines) if lines else "（无有效检索命中）"


def _render_generic(payload) -> str:
    """非列表 payload：转字符串并截断，避免超大 blob 进上下文"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    text = text.strip().replace("\n", " ")
    return text[:300] + ("…" if len(text) > 300 else "")


def slim_tool_message(msg: StoredMessage) -> StoredMessage:
    """把 role=tool 的历史消息正文替换为精简结构化渲染（不改原始存档，仅用于上下文视图）"""
    payload = None
    if msg.tool_payload:
        try:
            payload = json.loads(msg.tool_payload)
        except json.JSONDecodeError:
            payload = msg.tool_payload
    if isinstance(payload, list):
        rendered = _render_retrieval(payload)
    elif payload is not None:
        rendered = _render_generic(payload)
    else:
        rendered = msg.content
    # 就地复制精简形态（原始存档行不受影响）
    return msg.model_copy(update={"content": f"〔工具 {msg.tool_name or '检索'} 结果｜已精简〕\n{rendered}"})


def prefilter(messages: list[StoredMessage]) -> list[StoredMessage]:
    """上下文注入前的历史消息预处理：

    - 丢弃冗余（REDUNDANT）与已被摘要覆盖（非 active）的条目；
    - 工具结果（TOOL_RESULT）替换为精简渲染形态；
    - 其余按原序返回。
    """
    out: list[StoredMessage] = []
    for m in messages:
        if m.priority_tag == PriorityTag.REDUNDANT:
            continue
        if m.compression_state != "active":
            continue
        if m.role == "tool" or m.priority_tag == PriorityTag.TOOL_RESULT:
            out.append(slim_tool_message(m))
        else:
            out.append(m)
    return out
