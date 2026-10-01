"""实时行情服务（M4 扩展）：标的解析 → 新浪单标的直连取数 → 归一化格式化

覆盖标的空间（suggest3 类型码白名单）：
    - A 股个股/ETF/指数（sh/sz/bj 前缀 6 位码）
    - 港股（type 31，5 位码 → rt_hk 键）与港股/全球指数（type 33，字母码 → rt_hk 键）
    - 美股（type 41，字母代码 → gb_ 键）；期货/暗盘/场外基金（71/86/201/…）一律剔除

设计约束（与「行情防幻觉」规范一致）：
    - 数据源：hq.sinajs.cn 单标的实时端点 + suggest3.sinajs.cn 名称检索端点
      （本机网络阻断东方财富系，新浪通道实测可达；取数按市场分派独立解析器，
      语义不确定的字段（如美股成交额、港股指数成交量）宁缺毋滥、直接不展示）；
    - 名称解析优先级：显式代码（含 5 位港股裸码）→ 常见指数静态映射 →
      智能检索端点；全部落空时才调用 LLM 做错别字/近似标的推断（宁缺毋滥，
      置信度低于 QUOTE_FUZZY_MIN_CONFIDENCE 视为猜不准）；LLM 只负责“猜标的名”，
      数字一律来自数据源，推断命中时在回答开头用固定话术说明情况；
    - 所有网络/LLM 调用带硬超时，超时或无数据返回固定降级话术，不抛异常、
      不进入 LLM 生成——行情数字必须与数据源逐字一致，杜绝转写幻觉。
"""
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from src.agent.config import (
    QUOTE_DISCLAIMER,
    QUOTE_FUZZY_MIN_CONFIDENCE,
    QUOTE_SOURCE_NAME,
    QUOTE_TIMEOUT_SECONDS,
)
from src.agent.llm import llm
from src.agent.prompts import (
    QUOTE_FETCH_FAILED_ANSWER,
    QUOTE_FUZZY_NOTE,
    QUOTE_NOT_FOUND_ANSWER,
    QUOTE_SYMBOL_GUESS_SYSTEM_PROMPT,
)

# 常见指数名称 → 新浪代码（静态映射优先，免一次检索请求）
_INDEX_ALIASES = {
    "上证指数": "sh000001", "上证综指": "sh000001", "沪指": "sh000001",
    "深证成指": "sz399001", "深证成分指数": "sz399001",
    "沪深300": "sh000300", "沪深300指数": "sh000300",
    "上证50": "sh000016", "中证500": "sh000905", "中证1000": "sh000852",
    "创业板指": "sz399006", "科创50": "sh000688", "北证50": "bj899050",
}

_A_KEY_RE = re.compile(r"^(sh|sz|bj)\d{6}$", re.IGNORECASE)
_HK_CODE_RE = re.compile(r"^\d{5}$")
_HK_INDEX_CODE_RE = re.compile(r"^[A-Za-z]{2,8}$")
_US_CODE_RE = re.compile(r"^[A-Za-z.]{1,6}$")

# 从问句中剔除的行情噪声词（帮助纯行情词命中时提取标的名称）
_QUOTE_NOISE_RE = re.compile(
    r"(实时行情|最新价|最新净值|单位净值|累计净值|开盘价|收盘价|涨跌幅|涨跌额|"
    r"股价|现价|报价|行情|今开|昨收|涨停|跌停|换手率|多少钱|多少点|什么价|现在价格|"
    r"今日价格|现在|今天|今日|多少|查询|查一下|查下|看下|看一下|帮我查|帮我|价格|净值|指数|请|吗|呀|啊|呢|[，。！？、\s])"
)
_DATE_RE = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}")
_TIME_RE = re.compile(r"\d{2}:\d{2}:\d{2}")

# suggest3 是轻量端点（实测 <1s），单独收紧超时，给后续 LLM 纠错路径留预算
_SUGGEST_TIMEOUT = 5.0

_HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}


class _SymbolGuess(BaseModel):
    """LLM 标的推断结构化输出（仅用于纠错，不产出任何行情数字）"""
    canonical: str | None = Field(None, description="推断出的标准标的名称或代码；拿不准为 null")
    confidence: float = Field(0.0, ge=0, le=1, description="对该推断的把握")


_structured_llm = llm.with_structured_output(_SymbolGuess)


def _run_with_timeout(fn, timeout: float):
    """线程池硬超时执行同步网络/LLM 调用：超时抛 FutTimeout，由调用方降级"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn).result(timeout=timeout)


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:~\"'“”‘’（）()【】\[\]]+", "", text or "")


def _guess_prefix(code: str) -> str:
    """6 位纯代码补交易所前缀：5/6/9→沪，0/1/2/3→深，4/8→北"""
    head = code[0]
    if head in ("5", "6", "9"):
        return "sh"
    if head in ("4", "8"):
        return "bj"
    return "sz"


# ---------- 名称检索（suggest3，带市场类型白名单） ----------

def _entry_to_key(fields: list[str]) -> str | None:
    """suggest3 单条目 → 新浪取数键；白名单外市场（期货/暗盘/场外基金等）返回 None

    条目格式：显示名,类型码,代码,全码键,别名,,全名,...（type 11=A股 31=港股 33=指数 41=美股）
    """
    if len(fields) < 4:
        return None
    ftype, fcode, ffull = fields[1], fields[2], fields[3]
    if _A_KEY_RE.match(ffull):
        return ffull.lower()
    if ftype == "31" and _HK_CODE_RE.match(fcode):
        return "rt_hk" + fcode
    if ftype == "33" and _HK_INDEX_CODE_RE.match(fcode):
        return "rt_hk" + fcode.upper()
    if ftype == "41" and _US_CODE_RE.match(fcode):
        return "gb_" + fcode.lower()
    return None


def _suggest_lookup(key: str) -> tuple[str, str] | None:
    """新浪智能检索端点：名称关键词 → 首个白名单内标的的 (取数键, 条目显示名)"""
    url = f"https://suggest3.sinajs.cn/suggest/type=&key={quote(key)}"
    resp = httpx.get(url, headers=_HEADERS, timeout=_SUGGEST_TIMEOUT)
    resp.raise_for_status()
    body = resp.content.decode("gbk", errors="ignore")
    for entry in re.findall(r'"([^"]+)"', body):
        for item in entry.split(";"):
            fields = item.split(",")
            code = _entry_to_key(fields)
            if code:
                return code, fields[0].strip()
    return None


# ---------- 标的解析（快速路径；LLM 纠错在 get_market_quote 里兜底） ----------

def _resolve_explicit(s: str, question: str) -> str | None:
    """显式代码：带前缀 A 股码 / 6 位裸码 / 5 位港股裸码；解析不出返回 None"""
    if _A_KEY_RE.match(s):
        return s.lower()
    if re.fullmatch(r"\d{6}", s):
        # 裸码歧义位（如 000001）：问句点名「指数」且静态映射能命中则走指数，否则按交易所前缀启发式
        if "指数" in question or "指" in s:
            for alias, code in _INDEX_ALIASES.items():
                if alias in question and code.endswith(s):
                    return code
        return _guess_prefix(s) + s
    if _HK_CODE_RE.match(s):
        return "rt_hk" + s  # A 股代码均为 6 位，5 位裸码按港股理解
    return None


def _similar(a: str, b: str) -> bool:
    """归一化同义判断：忽略大小写/标点空白，相等或包含即视为用户本意（口语简搭不额外说明）"""
    na, nb = _normalize(a).lower(), _normalize(b).lower()
    return bool(na) and bool(nb) and (na == nb or na in nb or nb in na)


def _resolve_by_name(text: str) -> tuple[str, tuple[str, str] | None] | None:
    """名称路径：常见指数静态映射 → 智能检索（原文与剔除行情词后的残余文本都尝试）

    检索命中但条目名与输入明显不同（错别字/模糊命中）时，连带返回 (输入词, 标准名)
    供上层在回答中说明情况；命中本身仍由数据源精确解析，LLM 不参与。
    """
    for alias, code in _INDEX_ALIASES.items():
        if alias in text:
            return code, None
    cleaned = _QUOTE_NOISE_RE.sub("", text).strip()
    for key in filter(None, (text, cleaned)):
        try:
            hit = _run_with_timeout(lambda k=key: _suggest_lookup(k), _SUGGEST_TIMEOUT + 2)
        except Exception:
            continue
        if hit:
            code, name = hit
            fuzzy = None if _similar(key, name) else (cleaned or key, name)
            return code, fuzzy
    return None


def resolve_sina_code(symbol: str, question: str = "") -> tuple[str | None, tuple[str, str] | None]:
    """把用户标的（代码/名称）解析为新浪取数键

    返回 (取数键, 模糊命中说明)；解不出时返回 (None, None)，交上层 LLM 纠错。
    """
    s = (symbol or "").strip()
    if s:
        code = _resolve_explicit(s, question)
        if code:
            return code, None
    for text in filter(None, (s, (question or "").strip())):
        hit = _resolve_by_name(text)
        if hit:
            return hit
    return None, None


# ---------- 取数与按市场分派解析 ----------

def _fetch_body(sina_code: str) -> str | None:
    """单标的实时直连取数，返回引号内原始数据串；无数据返回 None"""
    resp = httpx.get(
        f"https://hq.sinajs.cn/list={sina_code}", headers=_HEADERS, timeout=QUOTE_TIMEOUT_SECONDS
    )
    resp.raise_for_status()
    body = resp.content.decode("gbk", errors="ignore")
    m = re.search(r'"(.*)"', body)
    if not m or not m.group(1).strip():
        return None
    return m.group(1)


def _quote_time(body: str) -> str:
    ts, tm = _DATE_RE.search(body), _TIME_RE.search(body)
    return f"{ts.group()} {tm.group()}" if ts and tm else "未知时间"


def _finish(body: str, snapshot: dict) -> dict | None:
    """校验必备字段并统一计算涨跌；价格展示一律优先用数据源原串（防转写幻觉、免科学计数法）"""
    price, prev = snapshot.get("price"), snapshot.get("prev_close")
    if price is None or not prev:
        return None
    change = price - prev
    snapshot.setdefault("price_disp", f"{price:.4g}")
    snapshot.setdefault("prev_disp", f"{prev:.4g}")
    snapshot.update(
        change=round(change, 4),
        change_pct=round(change / prev * 100, 2),
        change_disp=f"{change:+.4g}",
        change_pct_disp=f"{change / prev * 100:+.2f}%",
        quote_time=_quote_time(body),
        source=QUOTE_SOURCE_NAME,
    )
    return snapshot


def _f(fields: list[str], i: int) -> float | None:
    """安全取浮点：越界/非数字返回 None（可选字段用）"""
    try:
        return float(fields[i])
    except (IndexError, ValueError):
        return None


def _fetch_cn(key: str) -> dict | None:
    """A 股个股/ETF/指数：0名称 1今开 2昨收 3最新价 4最高 5最低 8成交量(股) 9成交额(元)"""
    try:
        body = _fetch_body(key)
        if not body:
            return None
        fields = body.split(",")
        if len(fields) < 10:
            return None
        snap = {
            "name": fields[0], "sina_code": key, "market": "A股", "currency": "CNY",
            "price": _f(fields, 3), "prev_close": _f(fields, 2),
            "price_disp": fields[3].strip(), "prev_disp": fields[2].strip(),
            "open": _f(fields, 1), "high": _f(fields, 4), "low": _f(fields, 5),
            "open_disp": fields[1].strip(), "high_disp": fields[4].strip(), "low_disp": fields[5].strip(),
            "volume": _f(fields, 8), "amount": _f(fields, 9),
        }
    except Exception:
        return None
    return _finish(body, snap)


def _fetch_hk(key: str) -> dict | None:
    """港股/港股指数 rt_hk*：0英文名 1中文名 2今开 3昨收 4最高 5最低 6最新价 17日期 18时间

    指数（字母键）的 11/12 字段语义与个股不一致且实测对不上，量能两列直接不展示。
    """
    try:
        body = _fetch_body(key)
        if not body:
            return None
        fields = body.split(",")
        if len(fields) < 19:
            return None
        is_stock = _HK_CODE_RE.match(key[len("rt_hk"):]) is not None
        snap = {
            "name": fields[1] or fields[0], "sina_code": key,
            "market": "港股" if is_stock else "港股指数", "currency": "HKD",
            "price": _f(fields, 6), "prev_close": _f(fields, 3),
            "price_disp": fields[6].strip(), "prev_disp": fields[3].strip(),
            "open": _f(fields, 2), "high": _f(fields, 4), "low": _f(fields, 5),
            "open_disp": fields[2].strip(), "high_disp": fields[4].strip(), "low_disp": fields[5].strip(),
            "volume": _f(fields, 12) if is_stock else None,
            "amount": _f(fields, 11) if is_stock else None,
        }
    except Exception:
        return None
    return _finish(body, snap)


def _fetch_us(key: str) -> dict | None:
    """美股 gb_*：0名称 1最新价 2涨跌幅% 3时间戳(北京时间) 4涨跌额 5今开 6最高 7最低 10成交量(股)

    昨收用「最新价−涨跌额」反推；11 之后的字段（盘前/盘后、成交额等）语义不稳定，不展示。
    """
    try:
        body = _fetch_body(key)
        if not body:
            return None
        fields = body.split(",")
        if len(fields) < 8:
            return None
        price, prev = _f(fields, 1), None
        change = _f(fields, 4)
        if price is not None and change is not None:
            prev = price - change
        snap = {
            "name": fields[0], "sina_code": key, "market": "美股", "currency": "USD",
            "price": price, "prev_close": prev,
            "price_disp": fields[1].strip(),
            "open": _f(fields, 5), "high": _f(fields, 6), "low": _f(fields, 7),
            "open_disp": fields[5].strip(), "high_disp": fields[6].strip(), "low_disp": fields[7].strip(),
            "volume": _f(fields, 10), "amount": None,
        }
        if snap["prev_close"] is None:
            return None
        result = _finish(body, snap)
        if result is not None:
            ts = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", body)
            result["quote_time"] = f"{ts.group()}（北京时间）" if ts else "未知时间"
        return result
    except Exception:
        return None


def fetch_quote(sina_code: str) -> dict | None:
    """按取数键前缀分派市场解析器；各市场数字字段独立适配，解析失败返回 None"""
    if sina_code.startswith("rt_hk"):
        result = _fetch_hk(sina_code)
    elif sina_code.startswith("gb_"):
        result = _fetch_us(sina_code)
    else:
        result = _fetch_cn(sina_code)
    return result


# ---------- LLM 错名/近似标的推断（仅快速路径全部落空后触发一次） ----------

def _guess_canonical(symbol: str, question: str) -> str | None:
    """让 LLM 从错别字/口语输入推断标准标的名；低置信或异常一律返回 None（宁缺毋滥）"""
    raw = (symbol or question or "").strip()
    if not raw:
        return None
    try:
        result = _run_with_timeout(
            lambda: _structured_llm.invoke(
                [
                    {"role": "system", "content": QUOTE_SYMBOL_GUESS_SYSTEM_PROMPT},
                    {"role": "user", "content": f"标的输入：{symbol or '（未单独给出）'}\n完整问句：{question or symbol}"},
                ]
            ),
            QUOTE_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    if not result or not result.canonical:
        return None
    canonical = result.canonical.strip()
    # 与原始输入无实质差异（快速路径已试过没中）或把握不足，均视为猜不准
    if not canonical or _similar(canonical, raw):
        return None
    if result.confidence < QUOTE_FUZZY_MIN_CONFIDENCE:
        return None
    return canonical


def _fmt_amount(v: float | None) -> str | None:
    """成交额/量转可读单位：亿/万；缺失返回 None（对应行不展示）"""
    if v is None:
        return None
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f} 万"
    return f"{v:.0f}"


def get_market_quote(symbol: str, question: str = "") -> dict:
    """行情查询统一入口（供工具层调用），永不抛异常。

    返回：{"ok": bool, "markdown": 整理好的回答, "data": 快照 | None}
    """
    # 说明里展示用户原始问句（优先）而非意图抽取/清洗后的中间变量，避免暴露抽取错误误导用户
    raw_input = (question or symbol or "").strip()
    try:
        try:
            code, fuzzy = _run_with_timeout(
                lambda: resolve_sina_code(symbol, question), QUOTE_TIMEOUT_SECONDS
            )
            note = None
            if code is None:
                # 快速路径全落空 → LLM 推断最接近标的，命中后仅再做一次完整解析（不再触发 LLM，无递归）
                canonical = _guess_canonical(symbol, question)
                if canonical:
                    code, _ = _run_with_timeout(
                        lambda: resolve_sina_code(canonical, ""), QUOTE_TIMEOUT_SECONDS
                    )
                    if code:
                        note = QUOTE_FUZZY_NOTE.format(raw=raw_input, canonical=canonical)
            elif fuzzy:
                # 检索端点模糊命中（错别字/近似名）：同样在回答中说明情况
                note = QUOTE_FUZZY_NOTE.format(raw=raw_input, canonical=fuzzy[1])
            if not code:
                return {"ok": False, "markdown": QUOTE_NOT_FOUND_ANSWER, "data": None}
            quote = _run_with_timeout(lambda: fetch_quote(code), QUOTE_TIMEOUT_SECONDS)
            if not quote:
                return {"ok": False, "markdown": QUOTE_NOT_FOUND_ANSWER, "data": None}
        except FutTimeout:
            # 解析/取数阶段超时仍属数据源问题，与整体异常同话术降级
            return {"ok": False, "markdown": QUOTE_FETCH_FAILED_ANSWER, "data": None}
        markdown = _format_markdown(quote)
        if note:
            markdown = f"> {note}\n\n{markdown}"
            quote["resolved_from"] = raw_input
        return {"ok": True, "markdown": markdown, "data": quote}
    except Exception:
        # 数据源不可达等未预期异常统一降级话术，不阻断问答链路
        return {"ok": False, "markdown": QUOTE_FETCH_FAILED_ANSWER, "data": None}


def _format_markdown(q: dict) -> str:
    """结构化快照 → Markdown 表格 + 来源 + 免责（数字全部来自数据源原串，不经 LLM 转写）"""
    rows = [
        ("最新价", q.get("price_disp") or f"{q['price']:.4g}"),
        ("涨跌额", q["change_disp"]),
        ("涨跌幅", q["change_pct_disp"]),
        ("昨收", q.get("prev_disp") or f"{q['prev_close']:.4g}"),
    ]
    if q.get("open") is not None:
        rows.append(("今开", q.get("open_disp") or f"{q['open']:.4g}"))
    if q.get("high") is not None and q.get("low") is not None:
        high = q.get("high_disp") or f"{q['high']:.4g}"
        low = q.get("low_disp") or f"{q['low']:.4g}"
        rows.append(("最高 / 最低", f"{high} / {low}"))
    if q.get("volume") is not None:
        rows.append(("成交量", _fmt_amount(q["volume"]) + " 股"))
    if q.get("amount") is not None:
        rows.append(("成交额", _fmt_amount(q["amount"]) + " " + q["currency"]))
    rows.append(("市场 / 计价货币", f"{q['market']} / {q['currency']}"))
    table = "\n".join([f"| 指标 | 数值 |", "| --- | --- |"] + [f"| {k} | {v} |" for k, v in rows])
    return (
        f"**{q['name']}（{q['sina_code']}）实时行情** · 行情时间：{q['quote_time']}\n\n"
        f"{table}\n\n"
        f"来源：{q['source']}\n\n"
        f"> {QUOTE_DISCLAIMER}"
    )
