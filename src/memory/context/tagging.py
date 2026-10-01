"""消息优先级打标：锁存实体 > 核心业务 > 工具结果 > 普通对话 > 冗余

依据角色、意图、作答类型与实体命中确定 PriorityTag；并提供关键实体抽取，
供锁存判定与摘要存活校验共用同一口径。
"""
import re

from src.memory.schemas import PriorityTag, StoredMessage

# 6 位 A 股/基金代码；带常见前缀的证券代码（如 sh600519 / 00700.HK）；沪深300 等指数名
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(?:[shzmbj]{0,2}\d{6}(?:\.[A-Z]{2})?|\d{5}\.\w{2})(?![0-9])")
_INDEX_NAME_RE = re.compile(r"(沪深\d+|上证\d+|恒生指数|创业板指|科创\d+|纳斯达克|标普\d+)")


def extract_entities(text: str) -> list[str]:
    """从正文抽取关键业务实体（证券代码 + 指数名），去重保序；供锁存与摘要存活校验共用"""
    if not text:
        return []
    found = _CODE_RE.findall(text) + _INDEX_NAME_RE.findall(text)
    seen: list[str] = []
    for e in found:
        if e and e not in seen:
            seen.append(e)
    return seen


def tag_message(msg: StoredMessage, latched: set[str] | None = None) -> PriorityTag:
    """为单条历史消息定优先级。

    判定优先级（从高到低）：
        1. 命中锁存实体集合或正文含证券代码/指数名 → LATCHED_ENTITY（强制常驻）
        2. role=tool → TOOL_RESULT
        3. 拒答/无素材固定话术 → REDUNDANT
        4. 实质性业务问答（kb_qa/news/quote 的 user 或 generated/quoted/searched 的 assistant）→ CORE_BUSINESS
        5. 其余（闲聊往来等）→ NORMAL
    """
    latched = latched or set()
    entities = extract_entities(msg.content)

    # 1. 锁存实体：命中当前会话锁存集合即强制常驻
    if entities and (latched & set(entities)):
        return PriorityTag.LATCHED_ENTITY

    # 2. 工具结果
    if msg.role == "tool":
        return PriorityTag.TOOL_RESULT

    # 3. 冗余：拒答话术不承载业务事实
    if msg.answer_type == "refused":
        return PriorityTag.REDUNDANT

    # 4. 核心业务问答
    if msg.role == "user" and msg.intent in ("kb_qa", "news_search", "quote_query"):
        return PriorityTag.CORE_BUSINESS
    if msg.role == "assistant" and msg.answer_type in ("generated", "quoted", "searched"):
        return PriorityTag.CORE_BUSINESS

    # 有证券代码但未被锁存上下文命中的业务消息，代码本身即关键实体，锁存
    if entities:
        return PriorityTag.LATCHED_ENTITY

    # 5. 普通对话（闲聊）
    return PriorityTag.NORMAL


def collect_latched(messages: list[StoredMessage]) -> set[str]:
    """汇总会话内出现过的关键实体，作为强制锁存集合（标的一旦提及即常驻至会话结束）"""
    latched: set[str] = set()
    for m in messages:
        latched.update(extract_entities(m.content))
    return latched
