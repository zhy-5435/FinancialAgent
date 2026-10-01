"""统一 Agent Loop（重构自「意图识别 → 静态四路分发」）

图结构（带环，plan → act 并发 → observe → replan | answer）：

    START → prepare（组装系统提示 + 多轮历史、注入候选工具、落库本轮 user）
        └─► reason ──(llm.bind_tools 自主选择工具)──► AIMessage
              ├─ 有 tool_calls ─► act ─► observe ─┬─(预算未超且无硬失败)─► reason（replan）
              └─ 无 tool_calls ───────────────────┴─────────────────────► finalize → END
                                                 └─(步数/墙钟超限或硬失败)─► finalize → END

- act 同一步内对多个 tool_calls 用 asyncio.gather 并发，逐个套 超时→重试→熔断→失败语义（registry.run_tool）。
- 三条防幻觉不变量在 finalize 硬保证（不依赖模型自觉）：行情数字逐字不转写、知识库无有效证据即拒答、简报无素材固定话术。
- invoke 与 stream 收敛为同一张图：aask_detail 走 ainvoke，aask_stream 走 astream，消除双链路漂移。
"""
from __future__ import annotations

import asyncio
import time
from operator import add
from typing import Annotated, Any, AsyncIterator, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from src.agent.config import (
    AGENT_LOOP_ENABLED,
    AGENT_LOOP_TIMEOUT_SECONDS,
    AGENT_MAX_STEPS,
)
from src.agent.intent import rule_classify
from src.agent.llm import llm
from src.agent.prompts import (
    AGENT_BUDGET_NOTE,
    AGENT_SYSTEM_PROMPT,
    NEWS_NO_EVIDENCE_ANSWER,
    REFUSAL_ANSWER,
)
from src.agent.registry import ToolResult, registry, run_tool
# 导入 tools 以触发工具向 registry 的注册（副作用）
import src.agent.tools  # noqa: F401  isort:skip
from src.agent.web_search import EMPTY_RESULT
from src.memory.memory_manager import memory_manager


class AgentState(TypedDict):
    question: str
    session_id: str                       # 空串=单轮无历史
    user_seq: int                         # 本轮 user 落库后的序号
    messages: Annotated[list[AnyMessage], add_messages]
    candidate_tools: list[str]            # 候选工具名；空=不限（全部）
    started_at: float                     # 本轮起始时刻（墙钟预算）
    step: int                             # 已用 reason 步数
    trace: Annotated[list[dict], add]     # 过程轨迹（plan / tool_result），供 SSE 与审计
    sources: list[dict]                   # 累积的 kb 有效切片（全局编号，供溯源）
    news_evidence: list[str]              # 累积的网搜素材块
    quote_markdown: str                   # 行情工具产出的逐字 markdown（终态直接采用）
    hard_tool_failed: bool                # hard 语义工具（kb 检索）失败 → 触发拒答
    budget_exceeded: bool                 # 步数/墙钟超限
    answer: str
    answer_type: str
    intent: str


# ---------- 辅助：消息转换与工具结果序列化 ----------

def _to_lc_messages(dicts: list[dict]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in dicts:
        role, content = m["role"], m["content"]
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    return out


def _render_kb_block(hits: list[dict], start: int) -> str:
    """把检索切片按全局编号渲染为可读证据块（编号与回答中的 [n] 溯源、sources 顺序对齐）。"""
    blocks = []
    for i, hit in enumerate(hits, start=start):
        clause = hit.get("clause_position") or "无条款号"
        blocks.append(
            f"[{i}] 相似度：{hit['similarity']} | 综合得分：{hit.get('score', '—')}\n"
            f"来源：{hit['doc_name']}（{hit['doc_type']}）| 版本 {hit.get('version') or '未知'} | {clause}\n"
            f"内容：{hit['content']}\n"
            f"原文摘录：{hit['original_text']}"
        )
    return "\n\n".join(blocks) if blocks else "（无相似度达标的知识库切片）"


def _tool_message_content(name: str, res: ToolResult, kb_start: int) -> str:
    """把工具结果转成给模型阅读的字符串（失败/降级给明确占位，供 replan 判断）。"""
    if not res.ok:
        return f"工具 {name} 调用失败：{res.error or '未知错误'}"
    if name == "search_knowledge":
        return _render_kb_block(res.data, kb_start)
    if name == "search_realtime_quote":
        data = res.data or {}
        if not data.get("ok"):
            return "行情工具未能取数：" + (data.get("markdown") or "无数据")
        return data.get("markdown", "")
    # web_search 返回证据块字符串
    return res.data or EMPTY_RESULT


# ---------- 节点 ----------

def prepare(state: AgentState) -> dict:
    """组装本轮初始消息（系统提示 + 多轮历史 + 用户问题）、确定候选工具集、多轮下先落库用户消息。"""
    question = state["question"]
    sid = state["session_id"]
    user_seq = 0
    if sid:
        user_seq = memory_manager.remember_user(sid, question, intent=None).seq

    candidates: list[str] = []
    if not AGENT_LOOP_ENABLED:
        # 应急回退：按规则预分类命中的意图把候选收窄到对应工具（近单轮形态）
        hit = rule_classify(question)
        candidates = registry.candidates_for_intent(hit.intent if hit else None) or []

    bundle = (
        memory_manager.build(sid, AGENT_SYSTEM_PROMPT, question, mode="turns",
                             before_seq=user_seq or None)
        if sid
        else None
    )
    msg_dicts = bundle.messages if bundle else [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    return {
        "user_seq": user_seq,
        "candidate_tools": candidates,
        "messages": _to_lc_messages(msg_dicts),
        "started_at": time.monotonic(),
        "step": 0,
        "trace": [],
        "sources": [],
        "news_evidence": [],
        "quote_markdown": "",
        "hard_tool_failed": False,
        "budget_exceeded": False,
    }


async def reason(state: AgentState) -> dict:
    """规划步：LLM 通过 bind_tools 自主选择工具（可一步多调用），或直接产出终答文本。"""
    tools = registry.tools_for(state["candidate_tools"] or None)
    bound = llm.bind_tools(tools)
    ai: AIMessage = await bound.ainvoke(state["messages"])

    update: dict[str, Any] = {"messages": [ai], "step": state["step"] + 1}
    plan_text = ai.content if isinstance(ai.content, str) else ""
    plan_text = (plan_text or "").strip()
    if getattr(ai, "tool_calls", None):
        update["trace"] = [{
            "type": "plan",
            "step": state["step"] + 1,
            "text": plan_text,
            "tool_calls": [{"name": tc["name"], "args": tc["args"]} for tc in ai.tool_calls],
        }]
    return update


def route_reason(state: AgentState) -> str:
    last = state["messages"][-1] if state["messages"] else None
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "act"
    return "finalize"


async def act(state: AgentState) -> dict:
    """执行步：对上一 AIMessage 的全部 tool_calls 并发执行（超时/重试/熔断由 run_tool 兜底）。"""
    ai: AIMessage = state["messages"][-1]
    tool_calls = list(ai.tool_calls or [])

    specs = [registry.get(tc["name"]) for tc in tool_calls]

    async def _run(spec, tc):
        if spec is None:
            return ToolResult(name=tc["name"], ok=False, degraded=True, error="未注册的工具")
        return await run_tool(spec, tc["args"])

    results = await asyncio.gather(*[_run(s, tc) for s, tc in zip(specs, tool_calls)])

    tool_msgs: list[ToolMessage] = []
    trace: list[dict] = []
    sources = list(state["sources"])
    news_evidence = list(state["news_evidence"])
    quote_markdown = state["quote_markdown"]
    hard_tool_failed = state["hard_tool_failed"]

    for tc, res in zip(tool_calls, results):
        name = tc["name"]
        kb_start = len(sources) + 1
        # 累积结构化产物（供 finalize 组装与溯源）
        if res.ok and name == "search_knowledge":
            sources.extend(res.data)
        elif res.ok and name == "web_search":
            news_evidence.append(res.data)
        elif res.ok and name == "search_realtime_quote":
            if (res.data or {}).get("ok"):
                quote_markdown = res.data.get("markdown", "")
        spec = registry.get(name)
        if not res.ok and spec is not None and spec.failure_class == "hard":
            hard_tool_failed = True

        content = _tool_message_content(name, res, kb_start)
        tool_msgs.append(ToolMessage(content=content, tool_call_id=tc["id"], name=name))
        trace.append({
            "type": "tool_result", "step": state["step"], "name": name,
            "ok": res.ok, "summary": _truncate(content, 300),
        })
        if state["session_id"]:
            memory_manager.remember_tool(
                state["session_id"], name, _truncate(content, 4000),
                payload=_tool_payload(name, res),
            )

    return {
        "messages": tool_msgs,
        "trace": trace,
        "sources": sources,
        "news_evidence": news_evidence,
        "quote_markdown": quote_markdown,
        "hard_tool_failed": hard_tool_failed,
    }


def observe(state: AgentState) -> dict:
    """观察步：判定步数 / 墙钟 / token 预算是否耗尽，置 budget_exceeded 供路由与 finalize 使用。"""
    max_steps = AGENT_MAX_STEPS if AGENT_LOOP_ENABLED else 1
    elapsed = time.monotonic() - state["started_at"]
    exceeded = state["step"] >= max_steps or elapsed >= AGENT_LOOP_TIMEOUT_SECONDS
    return {"budget_exceeded": exceeded}


def route_observe(state: AgentState) -> str:
    if state["hard_tool_failed"] or state["budget_exceeded"]:
        return "finalize"
    return "reason"


async def finalize(state: AgentState) -> dict:
    """终态组装：按三条防幻觉不变量决定 answer / answer_type / intent / sources，并落库本轮。"""
    trace = state["trace"]
    used = {e["name"] for e in trace if e.get("type") == "tool_result" and e.get("ok")}
    sources = state["sources"]
    quote_md = state["quote_markdown"]
    news = state["news_evidence"]

    # 模型给出的终答文本（仅当最后一条是无 tool_calls 的 AIMessage）
    last = state["messages"][-1] if state["messages"] else None
    model_text = ""
    if isinstance(last, AIMessage) and not getattr(last, "tool_calls", None):
        model_text = (last.content or "").strip() if isinstance(last.content, str) else ""

    answer: str
    answer_type: str
    intent: str

    if quote_md:
        # 行情终态：数字逐字采用工具 markdown，不经模型转写
        answer, answer_type, intent = quote_md, "quoted", "quote_query"
    elif "search_knowledge" in used and (state["hard_tool_failed"] or not sources):
        # 知识库无有效证据 / 检索硬失败 → 固定拒答（守住底线，不采信模型文本）
        answer, answer_type, intent = REFUSAL_ANSWER, "refused", "kb_qa"
    else:
        if not model_text:
            model_text = await _synthesize(state["messages"])
        if "search_knowledge" in used and sources:
            answer_type, intent = "generated", "kb_qa"
        elif "web_search" in used:
            if not news or all(n == EMPTY_RESULT for n in news):
                return _emit(state, NEWS_NO_EVIDENCE_ANSWER, "searched", "news_search", [])
            answer_type, intent = "searched", "news_search"
        elif not used:
            answer_type, intent = "chatted", "chitchat"
        else:
            answer_type, intent = "agentic", _pick_intent(used)
        answer = model_text
        if state["budget_exceeded"] and answer_type in ("generated", "searched", "agentic"):
            answer = answer + AGENT_BUDGET_NOTE

    out_sources = sources if answer_type in ("generated", "agentic") else []
    return _emit(state, answer, answer_type, intent, out_sources)


def _emit(state: AgentState, answer: str, answer_type: str, intent: str, sources: list[dict]) -> dict:
    """落库本轮助手回答并返回 finalize 的 state 增量。"""
    if state["session_id"]:
        memory_manager.remember_assistant(state["session_id"], answer, answer_type, intent=intent)
    return {"answer": answer, "answer_type": answer_type, "intent": intent, "sources": sources}


async def _synthesize(messages: list[AnyMessage]) -> str:
    """预算在工具执行中途耗尽时：不带工具再调一次模型，基于已有信息直接作答。"""
    guidance = HumanMessage(
        content="已达到本轮步数/时间上限，请仅依据目前已获取的信息直接给出回答，不要再请求调用任何工具。"
    )
    resp = await llm.ainvoke(list(messages) + [guidance])
    return (resp.content or "").strip() if isinstance(resp.content, str) else ""


def _pick_intent(used: set[str]) -> str:
    """多工具组合时按优先级派生 intent（值域仍是四分类）。"""
    if "search_realtime_quote" in used:
        return "quote_query"
    if "search_knowledge" in used:
        return "kb_qa"
    if "web_search" in used:
        return "news_search"
    return "chitchat"


def _tool_payload(name: str, res: ToolResult):
    """审计落库的结构化 payload（截断防超大 blob）。"""
    if not res.ok:
        return None
    if name == "search_knowledge":
        return res.data[:5]
    if name == "search_realtime_quote":
        return (res.data or {}).get("data")
    return _truncate(res.data or "", 4000)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


# ---------- 图构建 ----------

def build_agent_graph():
    """构建并编译带环的 Agent Loop 图（异步可 ainvoke / astream）。"""
    graph = StateGraph(AgentState)
    graph.add_node("prepare", prepare)
    graph.add_node("reason", reason)
    graph.add_node("act", act)
    graph.add_node("observe", observe)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "reason")
    graph.add_conditional_edges("reason", route_reason, {"act": "act", "finalize": "finalize"})
    graph.add_edge("act", "observe")
    graph.add_conditional_edges("observe", route_observe, {"reason": "reason", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile()


agent_graph = build_agent_graph()

_RUN_CFG = {"recursion_limit": 50}


def _initial_state(question: str, session_id: str | None) -> dict:
    return {
        "question": question,
        "session_id": session_id or "",
        "user_seq": 0,
        "messages": [],
        "candidate_tools": [],
        "started_at": 0.0,
        "step": 0,
        "trace": [],
        "sources": [],
        "news_evidence": [],
        "quote_markdown": "",
        "hard_tool_failed": False,
        "budget_exceeded": False,
        "answer": "",
        "answer_type": "",
        "intent": "",
    }


# ---------- 对外入口（异步原生 + 同步桥接） ----------

async def aask_detail(question: str, session_id: str | None = None) -> dict:
    """返回 answer / intent / answer_type / sources / steps（供 HTTP 服务层调用）。"""
    state = await agent_graph.ainvoke(_initial_state(question, session_id), config=_RUN_CFG)
    return {
        "answer": state["answer"],
        "intent": state["intent"],
        "answer_type": state["answer_type"],
        "sources": state["sources"],
        "steps": state.get("trace", []),
    }


def ask_detail(question: str, session_id: str | None = None) -> dict:
    return asyncio.run(aask_detail(question, session_id))


def ask(question: str, session_id: str | None = None) -> str:
    return asyncio.run(aask_detail(question, session_id))["answer"]


def _chunks(text: str, size: int = 48):
    for i in range(0, len(text), size):
        yield text[i:i + size]


async def aask_stream(question: str, session_id: str | None = None) -> AsyncIterator[dict]:
    """流式问答生成器：由 astream(updates) 驱动，产出 plan / step / meta / token 事件。

    与 ask_detail 走同一张图；plan/step 为新增过程事件（旧前端静默忽略），
    meta + token 保持既有契约形状（token 为定稿答案分片，保留打印机效果）。
    """
    async for node_update in agent_graph.astream(
        _initial_state(question, session_id), config=_RUN_CFG, stream_mode="updates"
    ):
        for node, delta in node_update.items():
            if not isinstance(delta, dict):
                continue
            if node == "reason":
                for e in delta.get("trace", []):
                    if e.get("type") == "plan":
                        yield {"event": "plan", "step": e["step"], "text": e["text"],
                               "tools": e["tool_calls"]}
            elif node == "act":
                for e in delta.get("trace", []):
                    if e.get("type") == "tool_result":
                        yield {"event": "step", "name": e["name"], "ok": e["ok"],
                               "summary": e.get("summary", "")}
            elif node == "finalize":
                yield {"event": "meta", "intent": delta["intent"],
                       "answer_type": delta["answer_type"], "sources": delta["sources"]}
                for chunk in _chunks(delta["answer"]):
                    yield {"event": "token", "text": chunk}
