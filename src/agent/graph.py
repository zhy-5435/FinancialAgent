"""问答图：LangGraph 编排 意图识别 → 四路分发 的问答流程

状态流转：
    START → classify_intent（LLM 结构化输出意图，低置信/异常回落 kb_qa）
        ├─ kb_qa       → retrieve（L2 检索 + 阈值过滤）
        │       ├─ 有有效切片 → generate（LLM 约束作答，强制溯源）
        │       └─ 无有效切片 → refuse（固定话术拒答，不消耗 LLM 调用）
        ├─ chitchat    → chat_generate（通用对话，无溯源，不消耗检索）
        ├─ news_search → news_generate（web_search 白名单网搜索证 → 财经简报生成；无素材固定话术）
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
    NEWS_BRIEF_SYSTEM_PROMPT,
    NEWS_NO_EVIDENCE_ANSWER,
    REFUSAL_ANSWER,
    SYSTEM_PROMPT,
    format_context,
)
from src.agent.tools import search_knowledge, search_realtime_quote, web_search
from src.agent.web_search import EMPTY_RESULT, SOURCE_DESC
from src.memory.memory_manager import memory_manager


class QAState(TypedDict):
    question: str                     # 用户问题
    session_id: str                   # 会话 ID（空串=单轮无历史，跳过记忆）
    user_seq: int                     # 本轮用户消息落库后的序号（构建上下文时排除本轮）
    intent: str                       # kb_qa / chitchat / news_search / quote_query
    search_query: str                 # 检索改写查询（缺省回退原问题）
    symbol: str                       # quote_query 抽取的标的（M4 行情工具消费）
    news_topic: str                   # news_search 抽取的资讯主题（M3 网搜工具消费）
    retrieved: list[dict]             # L2 原始命中切片
    valid_hits: Annotated[list[dict], add]  # 过滤后的有效证据（相似度达标）
    answer: str                       # 最终回答
    answer_type: str                  # generated/refused/chatted/searched/quoted


# ---------- 节点 ----------

def classify_intent(state: QAState) -> dict:
    """意图识别：四分类 + 实体抽取；低置信与异常均在 intent.classify 内回落 kb_qa

    多轮模式（session_id 非空）下，意图确定后立即落库本轮用户消息（携 intent），
    回写 user_seq 供后续构建上下文时排除本轮。
    """
    result = classify(state["question"])
    out = {
        "intent": result.intent,
        "search_query": result.rewritten_query or state["question"],
        "symbol": result.symbol or "",
        "news_topic": result.news_topic or "",
    }
    session_id = state.get("session_id")
    if session_id:
        msg = memory_manager.remember_user(session_id, state["question"], intent=result.intent)
        out["user_seq"] = msg.seq
    return out


def retrieve(state: QAState) -> dict:
    """调用 L2 检索工具（使用意图层改写后的查询），并按相似度阈值过滤出有效证据"""
    hits = search_knowledge.invoke({"query": state["search_query"]})
    valid = [h for h in hits if h.get("similarity", 0) >= SIMILARITY_THRESHOLD]
    return {"retrieved": hits, "valid_hits": valid}


def generate(state: QAState) -> dict:
    """LLM 约束作答：仅基于有效切片，回答附溯源来源；多轮下注入历史（仅供指代，不作事实来源）"""
    base_prompt = SYSTEM_PROMPT.format(context=format_context(state["valid_hits"]))
    messages = _assemble_messages(state, base_prompt, "block")
    response = llm.invoke(messages)
    _persist_turn_end(
        state, response.content, "generated",
        tool_name="search_knowledge", tool_payload=state["valid_hits"],
    )
    return {"answer": response.content, "answer_type": "generated"}


def refuse(state: QAState) -> dict:
    """低置信拒答：无命中或全部低于阈值时直接返回固定话术"""
    _persist_turn_end(
        state, REFUSAL_ANSWER, "refused",
        tool_name="search_knowledge", tool_content="（无有效命中）",
        tool_payload=state.get("retrieved", [])[:3],
    )
    return {"answer": REFUSAL_ANSWER, "answer_type": "refused"}


def chat_generate(state: QAState) -> dict:
    """闲聊分支：通用对话提示词，不检索知识库、不带溯源；多轮下作为历史轮次注入"""
    messages = _assemble_messages(state, CHAT_SYSTEM_PROMPT, "turns")
    response = llm.invoke(messages)
    _persist_turn_end(state, response.content, "chatted")
    return {"answer": response.content, "answer_type": "chatted"}


def news_generate(state: QAState) -> dict:
    """财经资讯分支（M3）：web_search 工具白名单网搜索证 → 财经简报生成

    仅访问权威信源白名单站点拉取原文作素材；无素材（检索为空/全失败）时
    直接固定话术短路，不消耗作答 LLM 调用。
    """
    topic = state["news_topic"] or state["search_query"]
    evidence = web_search.invoke({"query": topic})
    if evidence == EMPTY_RESULT:
        _persist_turn_end(
            state, NEWS_NO_EVIDENCE_ANSWER, "searched",
            tool_name="web_search", tool_content="（无素材）",
        )
        return {"answer": NEWS_NO_EVIDENCE_ANSWER, "answer_type": "searched"}
    base_prompt = NEWS_BRIEF_SYSTEM_PROMPT.format(
        web_evidence_blocks=evidence, web_sources=SOURCE_DESC
    )
    messages = _assemble_messages(state, base_prompt, "turns")
    response = llm.invoke(messages)
    _persist_turn_end(
        state, response.content, "searched",
        tool_name="web_search", tool_payload=_truncate(evidence, 4000),
    )
    return {"answer": response.content, "answer_type": "searched"}


def quote_answer(state: QAState) -> dict:
    """实时行情分支：行情工具取数并格式化（数字不经 LLM 转写，失败降级为固定话术）"""
    result = search_realtime_quote.invoke(
        {"symbol": state["symbol"], "query": state["question"]}
    )
    _persist_turn_end(
        state, result["markdown"], "quoted",
        tool_name="search_realtime_quote", tool_payload=result.get("data"),
    )
    return {"answer": result["markdown"], "answer_type": "quoted"}


# ---------- 路由 ----------

def _route_by_intent(state: QAState) -> str:
    return state["intent"]


def _route_after_retrieve(state: QAState) -> str:
    return "generate" if state["valid_hits"] else "refuse"


# ---------- 提示词组装与落库（invoke 与 stream 两条链路共用，避免漂移） ----------

def _kb_base_prompt(valid_hits: list[dict]) -> str:
    """知识库分支基系统提示：注入本轮检索证据块"""
    return SYSTEM_PROMPT.format(context=format_context(valid_hits))


def _news_base_prompt(evidence: str) -> str:
    """财经简报分支基系统提示：注入本轮网搜证据块"""
    return NEWS_BRIEF_SYSTEM_PROMPT.format(web_evidence_blocks=evidence, web_sources=SOURCE_DESC)


def _assemble_messages(state: QAState, base_prompt: str, mode: str) -> list[dict]:
    """组装调用模型的消息序列。

    - 单轮（session_id 为空）：[system, user]，无历史；
    - 多轮：交给上下文引擎按 4 档预算压缩历史后拼装（排除本轮刚落库的 user）。
    """
    question = state["question"]
    session_id = state.get("session_id")
    if not session_id:
        return [
            {"role": "system", "content": base_prompt},
            {"role": "user", "content": question},
        ]
    bundle = memory_manager.build(
        session_id, base_prompt, question,
        mode=mode, before_seq=state.get("user_seq") or None,
    )
    return bundle.messages


def _persist_turn_end(
    state: QAState, answer: str, answer_type: str,
    tool_name: str | None = None, tool_content: str = "", tool_payload=None,
) -> None:
    """本轮结果落库（审计存档）：先工具结果后助手回答；单轮模式跳过"""
    session_id = state.get("session_id")
    if not session_id:
        return
    if tool_name is not None:
        memory_manager.remember_tool(session_id, tool_name, tool_content, tool_payload)
    memory_manager.remember_assistant(session_id, answer, answer_type, intent=state.get("intent"))


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def build_graph():
    """构建并编译问答图，返回可 invoke 的 CompiledGraph"""
    graph = StateGraph(QAState)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("refuse", refuse)
    graph.add_node("chat_generate", chat_generate)
    graph.add_node("news_generate", news_generate)
    graph.add_node("quote_answer", quote_answer)

    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges(
        "classify_intent", _route_by_intent,
        {
            "kb_qa": "retrieve",
            "chitchat": "chat_generate",
            "news_search": "news_generate",
            "quote_query": "quote_answer",
        },
    )
    graph.add_conditional_edges(
        "retrieve", _route_after_retrieve,
        {"generate": "generate", "refuse": "refuse"},
    )
    for node in ("generate", "refuse", "chat_generate", "news_generate", "quote_answer"):
        graph.add_edge(node, END)
    return graph.compile()


# 全局实例
qa_graph = build_graph()


def _initial_state(question: str, session_id: str | None) -> dict:
    """构造图的初始状态（session_id 为空即单轮无历史）"""
    return {
        "question": question,
        "session_id": session_id or "",
        "user_seq": 0,
        "intent": "",
        "search_query": "",
        "symbol": "",
        "news_topic": "",
        "retrieved": [],
        "valid_hits": [],
        "answer": "",
        "answer_type": "",
    }


def ask(question: str, session_id: str | None = None) -> str:
    """对外统一入口：输入问题，返回带溯源的回答（或拒答/占位话术）；session_id 非空则多轮"""
    return qa_graph.invoke(_initial_state(question, session_id))["answer"]


def ask_detail(question: str, session_id: str | None = None) -> dict:
    """供 HTTP 服务层调用：返回回答文本 + 意图 + 作答类型 + 溯源切片列表

    返回结构：
        answer      最终回答文本
        intent      识别意图（kb_qa/chitchat/news_search/quote_query）
        answer_type generated=基于有效切片作答 / refused=无有效证据拒答
                    / chatted=闲聊对话 / searched=财经简报（白名单网搜证据生成）
                    / quoted=实时行情快照（表格化，不经 LLM）
        sources     有效证据切片列表（非 kb_qa 分支与拒答时为空；
                    简报来源站点与 URL 已内嵌于回答文本）
    """
    state = qa_graph.invoke(_initial_state(question, session_id))
    return {
        "answer": state["answer"],
        "intent": state["intent"],
        "answer_type": state["answer_type"],
        "sources": state["valid_hits"],
    }


def _stream_and_persist(state: dict, messages: list[dict], answer_type: str, *,
                        tool_name: str | None = None, tool_content: str = "", tool_payload=None):
    """流式产出增量 token 并在结束后落库本轮（工具结果 + 助手回答）"""
    collected: list[str] = []
    for chunk in llm.stream(messages):
        if chunk.content:
            collected.append(chunk.content)
            yield {"event": "token", "text": chunk.content}
    _persist_turn_end(
        state, "".join(collected), answer_type,
        tool_name=tool_name, tool_content=tool_content, tool_payload=tool_payload,
    )


def ask_stream(question: str, session_id: str | None = None):
    """流式作答生成器：意图路由后按分支产出 meta 事件与增量文本

    与 qa_graph 同一套「意图识别 → 分支作答」逻辑，仅为 SSE 输出重排为生成器；
    refuse / 无素材简报 / 行情快照分支不经作答 LLM，单次产出固定或格式化文本。
    session_id 非空时：意图确定后先落库本轮用户消息，再按上下文引擎组装消息，结束后落库结果。
    事件结构：
        {"event": "meta",  "intent": ..., "answer_type": ..., "sources": [...]}
        {"event": "token", "text": 增量文本}
    """
    sid = session_id or ""
    result = classify(question)
    intent = result.intent
    question_for_kb = result.rewritten_query or question

    user_seq = 0
    if sid:
        user_seq = memory_manager.remember_user(sid, question, intent=intent).seq
    state = {
        "question": question, "session_id": sid, "user_seq": user_seq, "intent": intent,
        "search_query": question_for_kb, "symbol": result.symbol or "",
        "news_topic": result.news_topic or "", "retrieved": [], "valid_hits": [],
    }

    if intent == "kb_qa":
        hits = search_knowledge.invoke({"query": question_for_kb})
        valid = [h for h in hits if h.get("similarity", 0) >= SIMILARITY_THRESHOLD]
        state["retrieved"], state["valid_hits"] = hits, valid
        yield {"event": "meta", "intent": intent,
               "answer_type": "generated" if valid else "refused", "sources": valid}
        if not valid:
            _persist_turn_end(
                state, REFUSAL_ANSWER, "refused",
                tool_name="search_knowledge", tool_content="（无有效命中）", tool_payload=hits[:3],
            )
            yield {"event": "token", "text": REFUSAL_ANSWER}
            return
        messages = _assemble_messages(state, _kb_base_prompt(valid), "block")
        yield from _stream_and_persist(
            state, messages, "generated",
            tool_name="search_knowledge", tool_payload=valid,
        )
    elif intent == "chitchat":
        yield {"event": "meta", "intent": intent, "answer_type": "chatted", "sources": []}
        messages = _assemble_messages(state, CHAT_SYSTEM_PROMPT, "turns")
        yield from _stream_and_persist(state, messages, "chatted")
    elif intent == "news_search":
        # 与 news_generate 节点同一链路：白名单网搜索证 → 财经简报生成
        topic = result.news_topic or question
        yield {"event": "meta", "intent": intent, "answer_type": "searched", "sources": []}
        evidence = web_search.invoke({"query": topic})
        if evidence == EMPTY_RESULT:
            _persist_turn_end(
                state, NEWS_NO_EVIDENCE_ANSWER, "searched",
                tool_name="web_search", tool_content="（无素材）",
            )
            yield {"event": "token", "text": NEWS_NO_EVIDENCE_ANSWER}
            return
        messages = _assemble_messages(state, _news_base_prompt(evidence), "turns")
        yield from _stream_and_persist(
            state, messages, "searched",
            tool_name="web_search", tool_payload=_truncate(evidence, 4000),
        )
    else:
        # 行情分支：工具取数后单帧产出格式化快照（不经 LLM，与图节点同一链路）
        qres = search_realtime_quote.invoke(
            {"symbol": result.symbol or "", "query": question}
        )
        yield {"event": "meta", "intent": intent, "answer_type": "quoted", "sources": []}
        _persist_turn_end(
            state, qres["markdown"], "quoted",
            tool_name="search_realtime_quote", tool_payload=qres.get("data"),
        )
        yield {"event": "token", "text": qres["markdown"]}
