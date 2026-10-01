"""字段级 PII 脱敏：入库前对身份证/手机号/银行卡/邮箱做部分遮蔽，保留首尾可辨识

设计（与规划一致）：
    - 零新增依赖，纯正则；MASKING_ENABLED 可关；
    - 仅处理存档正文，遮蔽后文本仍保留足够业务语义供多轮上下文理解（PII 本身非业务事实）；
    - 各类 PII 独立处理，顺序无冲突（邮箱先于数字类，避免 @ 前后串被数字规则误伤）。
"""
import re

from src.memory.config import MASKING_ENABLED

# 邮箱：保留用户名首字符与完整域名
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])([A-Za-z0-9._%+-]*)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# 18 位身份证（地区6+生日8+顺序3+校验1，校验位可为 X/x）：保留前 6 后 4
_ID_RE = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[0-9Xx])(?!\d)")
# 中国大陆手机号：保留前 3 后 4
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
# 银行卡号（16~19 位连续数字）：保留前 4 后 4
_BANK_RE = re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)")


def _mask_email(m: re.Match) -> str:
    return f"{m.group(1)}***@{m.group(3)}"


def _mask_id(m: re.Match) -> str:
    return f"{m.group(1)}{'*' * 8}{m.group(2)}"


def _mask_phone(m: re.Match) -> str:
    return f"{m.group(1)}****{m.group(3)}"


def _mask_bank(m: re.Match) -> str:
    star_len = len(m.group(0)) - len(m.group(1)) - len(m.group(2))
    return f"{m.group(1)}{'*' * star_len}{m.group(2)}"


def mask_pii(text: str) -> str:
    """对正文做 PII 脱敏；关闭开关或空文本原样返回。

    处理顺序：邮箱 → 身份证 → 手机号 → 银行卡。身份证/手机号/银行卡均以数字串呈现，
    先用固定长度边界各自匹配，避免相互吞并（银行卡用 16~19 位长串，手机号 11 位、身份证 18 位
    各自有专属前后缀约束，命中即遮蔽）。
    """
    if not text or not MASKING_ENABLED:
        return text
    out = _EMAIL_RE.sub(_mask_email, text)
    out = _ID_RE.sub(_mask_id, out)
    out = _PHONE_RE.sub(_mask_phone, out)
    out = _BANK_RE.sub(_mask_bank, out)
    return out
