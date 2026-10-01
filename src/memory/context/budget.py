"""Token 预算档位判断：按可用预算占用比例落到 direct/warning/global/trim 四档"""
from src.memory.config import (
    CONTEXT_MAX_TOKENS,
    CONTEXT_OUTPUT_RESERVE,
    TIER_GLOBAL,
    TIER_TRIM,
    TIER_WARNING,
)

# 档位名 → 与 ContextBundle.tier / 测试断言一致的字符串
TIER_DIRECT = "direct"
TIER_WARNING_NAME = "warning"
TIER_GLOBAL_NAME = "global"
TIER_TRIM_NAME = "trim"


def available_budget() -> int:
    """可用于组装上下文的 token 预算 = 上下文窗口 - 输出预留（下限 1）"""
    return max(CONTEXT_MAX_TOKENS - CONTEXT_OUTPUT_RESERVE, 1)


def usage_ratio(total_tokens: int) -> float:
    return total_tokens / available_budget()


def classify_tier(total_tokens: int) -> tuple[str, float]:
    """返回 (档位名, 占用比例)。边界按闭左开右：[0,0.70)→direct，[0.70,0.82)→warning，
    [0.82,0.92)→global，[0.92,∞)→trim。"""
    ratio = usage_ratio(total_tokens)
    if ratio < TIER_WARNING:
        return TIER_DIRECT, ratio
    if ratio < TIER_GLOBAL:
        return TIER_WARNING_NAME, ratio
    if ratio < TIER_TRIM:
        return TIER_GLOBAL_NAME, ratio
    return TIER_TRIM_NAME, ratio
