"""会话记忆层提示词：摘要生成 + 关键实体存活清单 + kb_qa 历史注入防幻觉框定语

金融可靠性约束沿用 L3 主链路：历史仅辅助理解指代与连贯，绝不作为事实来源。
"""

# ---------- 摘要生成 ----------
SUMMARIZE_SYSTEM_PROMPT = """你是对话上下文压缩助手。把给定的历史对话压缩成一段「业务要点摘要」，供后续问答保持连贯。

要求：
1. 只保留业务事实与结论：用户问过哪些标的/产品/条款、给出的关键数字与结论、已确认的实体；
2. 剔除寒暄、重复、过程性描述；
3. 涉及的具体名称、代码、费率、期限、数字必须原样保留，不得改写或概括掉；
4. 不得新增历史中不存在的信息，不做推断；
5. 输出不超过 {max_tokens} token，用中文，单段或分点皆可。
"""

# 摘要后附加的关键实体存活清单块：当校验发现摘要遗漏关键实体时强制补挂
KEY_ENTITY_SUFFIX = """

【关键实体清单（必须保留，供后续指代）】
{entities}
"""

# ---------- 历史转录渲染（喂给摘要模型的输入体） ----------
TRANSCRIPT_HEADER = "========= 待压缩历史对话 =========\n"


def render_transcript(messages: list[dict]) -> str:
    """把 [{role, content}] 渲染为逐条转录文本，供摘要模型读取"""
    lines = []
    for m in messages:
        role = m.get("role", "user")
        label = {"user": "用户", "assistant": "助手", "tool": "工具结果", "system-summary": "既有摘要"}.get(role, role)
        lines.append(f"{label}：{m.get('content', '').strip()}")
    return TRANSCRIPT_HEADER + "\n".join(lines)


# ---------- kb_qa 历史注入防幻觉框定语 ----------
# 追加到知识库分支系统提示词之后，明确既往轮次的用途边界
KB_HISTORY_FRAMING = """

【多轮历史使用规则】
以下历史对话仅用于理解指代（如「它的费率」「上面那只」）与保持话题连贯，
**严禁将其作为任何条款、费率、期限、风险等级或数字的事实来源**；
本轮所有业务事实只能依据「本轮检索切片」作答，检索不足仍按既有规则拒答。

{history_block}
"""

# 无历史时不注入任何前缀
NO_HISTORY_BLOCK = "（本会话暂无历史对话）"


def render_history_block(history_messages: list[dict]) -> str:
    """把注入用的历史消息渲染成带标签的块，供拼进系统提示词的历史框定段"""
    if not history_messages:
        return NO_HISTORY_BLOCK
    parts = []
    for m in history_messages:
        role = m.get("role", "user")
        label = "用户" if role == "user" else ("助手" if role == "assistant" else "工具/摘要")
        parts.append(f"- {label}：{m.get('content', '').strip()}")
    return "\n".join(parts)
