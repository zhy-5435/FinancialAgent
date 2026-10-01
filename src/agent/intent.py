"""意图识别：LLM 结构化输出四分类（kb_qa / chitchat / news_search / quote_query）

分流策略（兼顾时延与可靠性）：
    1) 关键词/规则预分类（INTENT_RULES_ENABLED）：命中 news/quote/chitchat 直接短路返回，
       跳过一次阻塞式意图 LLM 调用（实测可省 ~10s）；规则只在强信号时触发，拿不准一律交给 LLM；
    2) 未命中规则 → LLM 结构化分类。

可靠性兜底（与「宁可拒答不乱答」的产品定位一致）：
    - 分类链路异常（LLM 不可达 / 输出不可解析）→ 按改造前单链路走 kb_qa；
    - 非 kb_qa 且置信度低于 INTENT_CONFIDENCE_THRESHOLD → 回落 kb_qa，
      保留检索改写与现有拒答底线，避免错误分流到闲聊/待建分支。
"""
import re
from typing import Literal

from pydantic import BaseModel, Field

from src.agent.config import INTENT_CONFIDENCE_THRESHOLD, INTENT_RULES_ENABLED
from src.agent.llm import llm
from src.agent.prompts import INTENT_SYSTEM_PROMPT

# 意图枚举：契约层（schemas.py / types.ts）与本模块共用同一组取值
IntentName = Literal["kb_qa", "chitchat", "news_search", "quote_query"]


class IntentResult(BaseModel):
    """意图分类结构化输出"""
    intent: IntentName = Field(..., description="四分类意图")
    confidence: float = Field(..., ge=0, le=1, description="本次分类的置信度")
    symbol: str | None = Field(None, description="quote_query 时抽取的标的代码或名称")
    news_topic: str | None = Field(None, description="news_search 时的资讯主题词")
    rewritten_query: str | None = Field(None, description="面向知识库检索的改写查询")


# with_structured_output 由 LLM 按 IntentResult 的 JSON Schema 强制输出
_structured_llm = llm.with_structured_output(IntentResult)

# ---------- 关键词/规则预分类（命中即短路，跳过意图 LLM） ----------
# 设计原则：只在「强信号」时分流，尽量保守——拿不准一律返回 None 交给 LLM，
# 避免把内部制度类「工作琐事」（差旅/考勤/岗位…）误判为闲聊，也避免把产品说明书
# 的「费率/风险等级/期限」类问题误判为行情/资讯。

# 实时行情强信号词（产品说明书问费率/风险等级不会用这些词）
# 注：故意不含「市值」「走势」——会与监管适当性「市值要求/市值门槛」、研究类问题误冲，宁可交给 LLM
_QUOTE_TOKENS = (
    "最新价", "现价", "报价", "股价", "实时行情", "行情", "开盘价", "收盘价",
    "今开", "昨收", "涨跌幅", "涨停", "跌停", "换手率", "最新净值", "单位净值",
    "累计净值", "多少钱", "什么价", "现在价格", "今日价格", "盘口",
)
# 6 位 A 股/基金代码（如 600519）
_CODE_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")

# 财经资讯强信号词（需与新闻/资讯语义直接相关，不含裸「今天/最近」以防误伤制度类查询）
_NEWS_TOKENS = (
    "新闻", "资讯", "快讯", "简报", "头条", "最新动态", "最新进展", "发生了什么",
    "市场回顾", "行情回顾", "盘后", "财经要闻", "行业新闻",
)

# 寒暄/身份/能力类纯闲聊：仅限短消息且整句归一化后命中触发词；
# 一旦夹杂实质业务内容（长度超阈）即不判闲聊，回落 LLM。
_CHITCHAT_TRIGGERS = (
    "你好", "您好", "你好呀", "你好啊", "哈喽", "嗨", "hi", "hello", "在吗", "你在吗",
    "你是谁", "你叫什么", "你的名字", "自我介绍", "你能做什么", "你能干什么", "你会什么",
    "你能帮我做什么", "谢谢", "谢了", "多谢", "感谢", "再见", "拜拜", "晚安", "早上好",
    "中午好", "晚上好", "讲个笑话", "说个笑话", "陪我聊聊", "聊聊天",
)
# 命中触发词后，若剩余内容仍超过该字数，视为夹带业务问题，不短路
_CHITCHAT_MAX_LEN = 12


def _normalize(text: str) -> str:
    """去掉空白与常见中英文标点，便于短消息的归一化匹配"""
    return re.sub(r"[\s，。！？、；：,.!?;:~\"'“”‘’（）()【】\[\]]+", "", text)


def rule_classify(question: str) -> IntentResult | None:
    """关键词/规则预分类：命中返回 IntentResult，未命中返回 None（交 LLM）。

    评估顺序 quote → news → chitchat：三者信号词互斥，先判信息量更大的行情/资讯，
    最后判需额外做「短消息」约束的闲聊，降低误分流概率。
    """
    q = question.strip()
    if not q:
        return None

    # 1) 实时行情：强行情词 或 独立 6 位代码
    if any(t in q for t in _QUOTE_TOKENS) or _CODE_RE.search(q):
        code = _CODE_RE.search(q)
        return IntentResult(
            intent="quote_query", confidence=0.9,
            symbol=(code.group() if code else None),
            rewritten_query=q,
        )

    # 2) 财经资讯：命中新闻/资讯语义信号词
    if any(t in q for t in _NEWS_TOKENS):
        return IntentResult(intent="news_search", confidence=0.9, rewritten_query=q)

    # 3) 纯闲聊：短句且归一化后以寒暄触发词起头、无夹带实质内容
    norm = _normalize(q)
    if len(norm) <= _CHITCHAT_MAX_LEN:
        low = q.lower()
        if any(t in low for t in _CHITCHAT_TRIGGERS):
            return IntentResult(intent="chitchat", confidence=0.9, rewritten_query=q)

    return None


def classify(question: str) -> IntentResult:
    """单条问题意图分类；异常与低置信均回落 kb_qa，永不抛错阻断问答主链路"""
    # 规则预分类命中即短路，省一次阻塞式意图 LLM 调用；置信度记 0.9 高于阈值不会被回退
    if INTENT_RULES_ENABLED:
        hit = rule_classify(question)
        if hit is not None:
            return hit

    try:
        result = _structured_llm.invoke([
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ])
    except Exception:
        # 分类不可用时等同 M1 改造前的单链路行为（检索→作答/拒答），不引入新失败面
        return IntentResult(intent="kb_qa", confidence=0.0, rewritten_query=question)

    if result.intent != "kb_qa" and result.confidence < INTENT_CONFIDENCE_THRESHOLD:
        result = result.model_copy(update={"intent": "kb_qa"})
    return result
