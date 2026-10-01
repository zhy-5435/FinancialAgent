"""上下文构建引擎主编排：固定系统提示常驻 → 打标 → 工具预处理 → 4 档预算判定 → 摘要/裁剪 → 组装视图

产出 ContextBundle：
    - mode="block"（kb_qa）：历史以带框定语的文块并入系统提示词，messages = [system, user]；
      事实只依据本轮检索，历史仅供指代。
    - mode="turns"（chitchat/news）：历史作为既往轮次注入，messages = [system, *history_turns, user]。

档位语义（占可用预算比例）：
    direct <70%：全量历史直接组装，不摘要，仅裁冗余、精简工具结果；
    warning 70~82%：最早低优先级(NORMAL/工具结果)局部摘要，最近 N 轮完整、锁存实体常驻；
    global  82~92%：全历史（除最近轮）生成全局摘要并校验关键实体，锁存实体常驻；
    trim    >92%：先做全局摘要，再直接裁剪最早的非锁存上下文。
"""
from src.memory.config import RECENT_FULL_ROUNDS, RECENT_KEEP_ROUNDS
from src.memory.context.budget import (
    TIER_DIRECT,
    TIER_TRIM_NAME,
    TIER_WARNING_NAME,
    available_budget,
    classify_tier,
)
from src.memory.context.summarizer import Summarizer
from src.memory.context.tagging import collect_latched, tag_message
from src.memory.context.tokenizer import estimate, estimate_messages
from src.memory.context.tool_prefilter import prefilter
from src.memory.prompts import KB_HISTORY_FRAMING, render_history_block
from src.memory.schemas import ContextBundle, PriorityTag, StoredMessage, TokenReport

_BLOCK_MODE = "block"
_TURNS_MODE = "turns"


class ContextBuilder:
    def build_context(
        self,
        session_id: str,
        base_system_prompt: str,
        question: str,
        history: list[StoredMessage],
        *,
        mode: str = _TURNS_MODE,
        extra_tokens_estimate: int = 0,
        summarizer: Summarizer | None = None,
        persist_summary: bool = True,
    ) -> ContextBundle:
        summarizer = summarizer or Summarizer()

        # 1. 打标 + 2. 工具预处理（丢弃冗余/已覆盖，精简工具结果）
        latched = collect_latched(history)
        tagged = [m.model_copy(update={"priority_tag": tag_message(m, latched)}) for m in history]
        prepared = prefilter(tagged)

        fresh_tokens = estimate(base_system_prompt) + estimate(question) + estimate(
            f"用户提问：{question}" if mode == _BLOCK_MODE else ""
        ) + extra_tokens_estimate

        # 3. 预算档位判断（按全量候选历史估算落档）
        full_msgs = _to_turns(prepared)
        total_full = fresh_tokens + estimate_messages(full_msgs)
        tier, ratio = classify_tier(total_full)

        # 4. 按档组装压缩后的历史
        summary_ref = None
        if tier == TIER_DIRECT or not prepared:
            compacted = prepared
        elif tier == TIER_WARNING_NAME:
            compacted, summary_ref = _apply_warning(
                session_id, prepared, summarizer, persist_summary
            )
        else:  # global / trim 均先全局摘要，trim 再硬裁剪
            compacted, summary_ref = _apply_global(
                session_id, prepared, summarizer, persist_summary
            )
            if tier == TIER_TRIM_NAME:
                compacted = _apply_trim(compacted, fresh_tokens)

        # 5. 组装最终消息视图
        history_for_prompt = [m for m in compacted if m.content.strip()]
        if mode == _BLOCK_MODE:
            framed_system = base_system_prompt + KB_HISTORY_FRAMING.format(
                history_block=render_history_block(_to_turns(history_for_prompt))
            )
            messages = [
                {"role": "system", "content": framed_system},
                {"role": "user", "content": question},
            ]
            system_for_report = framed_system
        else:
            messages = [{"role": "system", "content": base_system_prompt}]
            messages.extend(_to_turns(history_for_prompt))
            messages.append({"role": "user", "content": question})
            system_for_report = base_system_prompt

        report = _build_report(
            system_for_report, history_for_prompt, question, fresh_tokens, tier, ratio, summary_ref
        )
        return ContextBundle(
            session_id=session_id,
            system_prompt=system_for_report,
            messages=messages,
            tier=tier,
            summary_ref=summary_ref,
            token_report=report,
        )


# ---------- 各档变换 ----------

def _apply_warning(session_id, prepared, summarizer, persist) -> tuple[list[StoredMessage], str | None]:
    """预警档：最早的低优先级（工具结果/普通对话）局部摘要；锁存与核心业务保留；最近 N 轮完整"""
    old, recent = _split_recent_rounds(prepared, RECENT_FULL_ROUNDS)
    to_summarize = [m for m in old if m.priority_tag >= PriorityTag.TOOL_RESULT]
    keep_old = [m for m in old if m.priority_tag < PriorityTag.TOOL_RESULT]
    if not to_summarize:
        return prepared, None
    rec = summarizer.summarize(session_id, to_summarize, scope="local", persist=persist)
    return [rec_as_message(session_id, rec)] + keep_old + recent, rec.record_id


def _apply_global(session_id, prepared, summarizer, persist) -> tuple[list[StoredMessage], str | None]:
    """全局摘要档：最近 K 轮完整保留，其余（锁存除外）生成全局摘要并校验关键实体"""
    old, recent = _split_recent_rounds(prepared, RECENT_KEEP_ROUNDS)
    to_summarize = [m for m in old if m.priority_tag != PriorityTag.LATCHED_ENTITY]
    keep_latched = [m for m in old if m.priority_tag == PriorityTag.LATCHED_ENTITY]
    if not to_summarize:
        return prepared, None
    rec = summarizer.summarize(session_id, to_summarize, scope="global", persist=persist)
    return [rec_as_message(session_id, rec)] + keep_latched + recent, rec.record_id


def _apply_trim(compacted, fresh_tokens) -> list[StoredMessage]:
    """裁剪档：在预算仍超限时，从最早的非锁存条目起直接丢弃，直至落回预算内"""
    budget = available_budget()
    result = list(compacted)
    dropped = 0
    while result:
        total = fresh_tokens + estimate_messages(_to_turns(result))
        if total <= budget:
            break
        # 找最早的非锁存项丢弃；全为锁存则停止（宁可超限也不丢锁存）
        idx = next(
            (i for i, m in enumerate(result) if m.priority_tag != PriorityTag.LATCHED_ENTITY),
            None,
        )
        if idx is None:
            break
        result.pop(idx)
        dropped += 1
    return result


# ---------- 工具函数 ----------

def _split_recent_rounds(messages: list[StoredMessage], k_rounds: int) -> tuple[list, list]:
    """以 user 消息为轮次边界，切出最近 k_rounds 轮（含其前导 user）与更早部分"""
    user_positions = [i for i, m in enumerate(messages) if m.role == "user"]
    if not user_positions or k_rounds <= 0 or len(user_positions) <= 0:
        return messages, []
    # 最近 k 轮的起始 user 位置
    start = user_positions[-k_rounds] if len(user_positions) >= k_rounds else user_positions[0]
    return messages[:start], messages[start:]


def _to_turns(messages: list[StoredMessage]) -> list[dict]:
    """转为 chat 轮次：非 user 角色（assistant/tool/system-summary）统一并入 assistant，保证消息序列合法"""
    turns = []
    for m in messages:
        if not m.content.strip():
            continue
        role = "user" if m.role == "user" else "assistant"
        content = m.content
        if m.role == "system-summary":
            content = f"【历史要点摘要】\n{m.content}"
        elif m.role == "tool":
            content = f"【上文工具/检索结果】\n{m.content}"
        turns.append({"role": role, "content": content})
    return turns


def rec_as_message(session_id: str, rec) -> StoredMessage:
    """把压缩记录转为注入上下文的 system-summary 伪消息（锁存级，永不再被摘要/裁剪）"""
    return StoredMessage(
        message_id=f"summary-{rec.record_id}",
        session_id=session_id,
        seq=0,
        role="system-summary",
        content=rec.summary_text,
        priority_tag=PriorityTag.LATCHED_ENTITY,
        token_est=rec.summary_token_est,
    )


def _build_report(system, history, question, fresh_tokens, tier, ratio, summary_ref) -> TokenReport:
    return TokenReport(
        system_tokens=estimate(system),
        history_tokens=estimate_messages(_to_turns(history)),
        fresh_tokens=fresh_tokens,
        total_tokens=fresh_tokens + estimate_messages(_to_turns(history)),
        budget_tokens=available_budget(),
        usage_ratio=round(ratio, 4),
        tier=tier,
        summarized=summary_ref is not None,
    )
