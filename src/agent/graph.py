"""问答图：LangGraph 编排 意图识别 → 四路分发 的问答流程

状态流转：
    START → classify_intent（LLM 结构化输出意图，低置信/异常回落 kb_qa）
        ├─ kb_qa       → retrieve（L2 检索 + 阈值过滤）
        │       ├─ 有有效切片 → generate（LLM 约束作答，强制溯源）
        │       └─ 无有效切片 → refuse（固定话术拒答，不消耗 LLM 调用）
        ├─ chitchat    → chat_generate（通用对话，无溯源，不消耗检索）
        ├─ news_search → news_pending（占位话术，M3 接入网搜工具）
        └─ quote_query → quote_answer（行情工具取数→表格化呈现，不经 LLM，附来源与免责）
"""
from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from src.agent.config import SIMILARITY_THRESHOLD
from src.agent.intent import classify
from src.agent.llm import llm
from src.agent.prompts import (
    CHAT_SYSTEM_PROMPT,
    NEWS_PENDING_ANSWER,
    REFUSAL_ANSWER,
    SYSTEM_PROMPT,
    format_context,
)
from src.agent.tools import search_knowledge, search_realtime_quote


class QAState(TypedDict):
    question: str                     # 用户问题
    intent: str                       # kb_qa / chitchat / news_search / quote_query
    search_query: str                 # 检索改写查询（缺省回退原问题）
    symbol: str                       # quote_query 抽取的标的（M4 行情工具消费，M1 暂只透传）
    news_topic: str                   # news_search 抽取的资讯主题（M3 网搜工具消费）
    retrieved: list[dict]             # L2 原始命中切片
    valid_hits: Annotated[list[dict], add]  # 过滤后的有效证据（相似度达标）
    answer: str                       # 最终回答
    answer_type: str                  # generated/refused/chatted/searched/quoted


# ---------- 节点 ----------

def classify_intent(state: QAState) -> dict:
    """意图识别：四分类 + 实体抽取；低置信与异常均在 intent.classify 内回落 kb_qa"""
    result = classify(state["question"])
    return {
        "intent": result.intent,
        "search_query": result.rewritten_query or state["question"],
        "symbol": result.symbol or "",
        "news_topic": result.news_topic or "",
    }


def retrieve(state: QAState) -> dict:
    """调用 L2 检索工具（使用意图层改写后的查询），并按相似度阈值过滤出有效证据"""
    hits = search_knowledge.invoke({"query": state["search_query"]})
    valid = [h for h in hits if h.get("similarity", 0) >= SIMILARITY_THRESHOLD]
    return {"retrieved": hits, "valid_hits": valid}


def generate(state: QAState) -> dict:
    """LLM 约束作答：仅基于有效切片，回答附溯源来源"""
    response = llm.invoke(_kb_messages(state["question"], state["valid_hits"]))
    return {"answer": response.content, "answer_type": "generated"}


def refuse(state: QAState) -> dict:
    """低置信拒答：无命中或全部低于阈值时直接返回固定话术"""
    return {"answer": REFUSAL_ANSWER, "answer_type": "refused"}


def chat_generate(state: QAState) -> dict:
    """闲聊分支：通用对话提示词，不检索知识库、不带溯源"""
    response = llm.invoke(_chat_messages(state["question"]))
    return {"answer": response.content, "answer_type": "chatted"}


def news_pending(state: QAState) -> dict:
    """财经资讯分支占位：M3 接入网搜工具后替换为 检索→简报生成 链路"""
    return {"answer": NEWS_PENDING_ANSWER, "answer_type": "searched"}


def quote_answer(state: QAState) -> dict:
    """实时行情分支：行情工具取数并格式化（数字不经 LLM 转写，失败降级为固定话术）"""
    result = search_realtime_quote.invoke(
        {"symbol": state["symbol"], "query": state["question"]}
    )
    return {"answer": result["markdown"], "answer_type": "quoted"}


# ---------- 路由 ----------

def _route_by_intent(state: QAState) -> str:
    return state["intent"]


def _route_after_retrieve(state: QAState) -> str:
    return "generate" if state["valid_hits"] else "refuse"


# ---------- 提示词组装（invoke 与 stream 两条链路共用，避免漂移） ----------

def _kb_messages(question: str, valid_hits: list[dict]) -> list[dict]:
    """知识库约束作答的消息序列：SYSTEM 注入证据块 + 用户原问题"""
    prompt = SYSTEM_PROMPT.format(context=format_context(valid_hits))
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": question},
    ]


def _chat_messages(question: str) -> list[dict]:
    """闲聊分支的消息序列"""
    return [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]


def build_graph():
    """构建并编译问答图，返回可 invoke 的 CompiledGraph"""
    graph = StateGraph(QAState)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("refuse", refuse)
    graph.add_node("chat_generate", chat_generate)
    graph.add_node("news_pending", news_pending)
    graph.add_node("quote_answer", quote_answer)

    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges(
        "classify_intent", _route_by_intent,
        {
            "kb_qa": "retrieve",
            "chitchat": "chat_generate",
            "news_search": "news_pending",
            "quote_query": "quote_answer",
        },
    )
    graph.add_conditional_edges(
        "retrieve", _route_after_retrieve,
        {"generate": "generate", "refuse": "refuse"},
    )
    for node in ("generate", "refuse", "chat_generate", "news_pending", "quote_answer"):
        graph.add_edge(node, END)
    return graph.compile()


# 全局实例
qa_graph = build_graph()


def ask(question: str) -> str:
    """对外统一入口：输入问题，返回带溯源的回答（或拒答/占位话术）"""
    return qa_graph.invoke({"question": question})["answer"]


def ask_detail(question: str) -> dict:
    """供 HTTP 服务层调用：返回回答文本 + 意图 + 作答类型 + 溯源切片列表

    返回结构：
        answer      最终回答文本
        intent      识别意图（kb_qa/chitchat/news_search/quote_query）
        answer_type generated=基于有效切片作答 / refused=无有效证据拒答
                    / chatted=闲聊对话 / quoted=实时行情快照 / searched=资讯分支占位（M3 生效）
        sources     有效证据切片列表（非 kb_qa 分支与拒答时为空）
    """
    state = qa_graph.invoke({"question": question})
    return {
        "answer": state["answer"],
        "intent": state["intent"],
        "answer_type": state["answer_type"],
        "sources": state["valid_hits"],
    }


def ask_stream(question: str):
    """流式作答生成器：意图路由后按分支产出 meta 事件与增量文本

    与 qa_graph 同一套「意图识别 → 分支作答」逻辑，仅为 SSE 输出重排为生成器；
    refuse / 占位分支不消耗作答 LLM 调用，单次产出固定话术。
    事件结构：
        {"event": "meta",  "intent": ..., "answer_type": ..., "sources": [...]}
        {"event": "token", "text": 增量文本}
    """
    result = classify(question)
    intent = result.intent
    question_for_kb = result.rewritten_query or question

    if intent == "kb_qa":
        hits = search_knowledge.invoke({"query": question_for_kb})
        valid = [h for h in hits if h.get("similarity", 0) >= SIMILARITY_THRESHOLD]
        yield {"event": "meta", "intent": intent,
               "answer_type": "generated" if valid else "refused", "sources": valid}
        if not valid:
            yield {"event": "token", "text": REFUSAL_ANSWER}
            return
        messages = _kb_messages(question, valid)
    elif intent == "chitchat":
        yield {"event": "meta", "intent": intent, "answer_type": "chatted", "sources": []}
        messages = _chat_messages(question)
    elif intent == "quote_query":
        # 行情分支：工具取数后单帧产出格式化快照（不经 LLM，与图节点同一链路）
        qres = search_realtime_quote.invoke(
            {"symbol": result.symbol or "", "query": question}
        )
        yield {"event": "meta", "intent": intent, "answer_type": "quoted", "sources": []}
        yield {"event": "token", "text": qres["markdown"]}
        return
    else:
        yield {"event": "meta", "intent": intent, "answer_type": "searched", "sources": []}
        yield {"event": "token", "text": NEWS_PENDING_ANSWER}
        return

    for chunk in llm.stream(messages):
        if chunk.content:
            yield {"event": "token", "text": chunk.content}
