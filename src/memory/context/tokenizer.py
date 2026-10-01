"""字符启发式 token 估算：中文≈1 字 1 token，英文/数字按词长÷4 近似

零新增依赖（GLM 端点无 /encoding 接口，字符启发式对预算档位判断足够）。
同一估算口径贯穿「入库 token_est」与「组装实时估算」，保证档位判定可复现。
"""
import re

# 连续中文（含常见中文标点）逐字计；英文/数字连续串按整段计
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
_LATIN_RUN_RE = re.compile(r"[0-9A-Za-z._%+\-@]+")


def estimate(text: str) -> int:
    """估算一段文本的 token 数（向上取整，最小为 0）"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    latin_chars = sum(len(run) for run in _LATIN_RUN_RE.findall(text))
    latin_tokens = latin_chars / 4  # 英文经验值：约 4 字符 ≈ 1 token
    # 未被两类命中的其他符号按 1 字符 1 token 保守计入
    other = len(text) - cjk - latin_chars
    return int(cjk + latin_tokens + max(other, 0) + 0.999)


def estimate_messages(messages: list[dict]) -> int:
    """估算 OpenAI 风格消息序列 [{role, content}] 的总 token（含每条 ~4 的结构开销）"""
    total = 0
    for m in messages:
        total += estimate(m.get("content", "")) + 4
    return total
