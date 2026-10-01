"""问答图：LangGraph 编排 检索 → 过滤 → 作答/拒答 的基础流程

状态流转：
    START → retrieve（L2 工具检索 + 阈值过滤）
        ├─ 有有效切片 → generate（LLM 约束作答，强制溯源）
        └─ 无有效切片 → refuse（固定话术拒答，不消耗 LLM 调用）
"""
from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from src.agent.config import SIMILARITY_THRESHOLD
from src.agent.llm import llm
from src.agent.prompts import REFUSAL_ANSWER, SYSTEM_PROMPT, format_context
from src.agent.tools import search_knowledge


class QAState(TypedDict):
    question: str                     # 用户问题
    retrieved: list[dict]             # L2 原始命中切片
    valid_hits: Annotated[list[dict], add]  # 过滤后的有效证据（相似度达标）
    answer: str                       # 最终回答


def retrieve(state: QAState) -> dict:
    """调用 L2 检索工具，并按相似度阈值过滤出有效证据"""
    hits = search_knowledge.invoke({"query": state["question"]})
    valid = [h for h in hits if h.get("similarity", 0) >= SIMILARITY_THRESHOLD]
    return {"retrieved": hits, "valid_hits": valid}


def generate(state: QAState) -> dict:
    """LLM 约束作答：仅基于有效切片，回答附溯源来源"""
    prompt = SYSTEM_PROMPT.format(context=format_context(state["valid_hits"]))
    response = llm.invoke(
        [{"role": "system", "content": prompt},
         {"role": "user", "content": state["question"]}]
    )
    return {"answer": response.content}


def refuse(state: QAState) -> dict:
    """低置信拒答：无命中或全部低于阈值时直接返回固定话术"""
    return {"answer": REFUSAL_ANSWER}


def _route_after_retrieve(state: QAState) -> str:
    return "generate" if state["valid_hits"] else "refuse"


def build_graph():
    """构建并编译问答图，返回可 invoke 的 CompiledGraph"""
    graph = StateGraph(QAState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("refuse", refuse)

    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges(
        "retrieve", _route_after_retrieve,
        {"generate": "generate", "refuse": "refuse"},
    )
    graph.add_edge("generate", END)
    graph.add_edge("refuse", END)
    return graph.compile()


# 全局实例
qa_graph = build_graph()


def ask(question: str) -> str:
    """对外统一入口：输入问题，返回带溯源的回答（或拒答话术）"""
    return qa_graph.invoke({"question": question})["answer"]


def ask_detail(question: str) -> dict:
    """供 HTTP 服务层调用：返回回答文本 + 作答类型 + 溯源切片列表

    返回结构：
        answer      最终回答文本
        answer_type generated（基于有效切片作答）/ refused（无有效证据拒答）
        sources     有效证据切片列表（拒答时为空）
    """
    state = qa_graph.invoke({"question": question})
    valid_hits = state["valid_hits"]
    return {
        "answer": state["answer"],
        "answer_type": "generated" if valid_hits else "refused",
        "sources": valid_hits,
    }


def ask_stream(question: str):
    """流式作答生成器：检索完成后先产出 meta 事件，再逐 token 产出增量文本

    与 qa_graph 同一套「检索 → 阈值过滤 → 作答/拒答」逻辑，
    仅为 SSE 输出重排为生成器；拒答分支不消耗 LLM 调用，单次产出固定话术。
    事件结构：
        {"event": "meta",  "answer_type": ..., "sources": [...]}
        {"event": "token", "text": 增量文本}
    """
    hits = search_knowledge.invoke({"query": question})
    valid = [h for h in hits if h.get("similarity", 0) >= SIMILARITY_THRESHOLD]
    yield {"event": "meta", "answer_type": "generated" if valid else "refused", "sources": valid}

    if not valid:
        yield {"event": "token", "text": REFUSAL_ANSWER}
        return

    prompt = SYSTEM_PROMPT.format(context=format_context(valid))
    for chunk in llm.stream(
        [{"role": "system", "content": prompt},
         {"role": "user", "content": question}]
    ):
        if chunk.content:
            yield {"event": "token", "text": chunk.content}
