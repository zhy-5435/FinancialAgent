"""统一 Agent Loop（重构自「意图识别 → 静态四路分发」）

图结构（带环，plan → act 并发 → observe → replan | answer）：

    START → prepare（组装系统提示 + 多轮历史、注入候选工具、落库本轮 user）
        └─► reason ──(llm.bind_tools 自主选择工具)──► AIMessage
              ├─ 有 tool_calls ─► act ─► observe ─┬─(预算未超且无硬失败)─► reason（replan）
              └─ 无 tool_calls ───────────────────┴─────────────────────► finalize → END
                                                 └─(步数/墙钟超限或硬失败)─► finalize → END

- act 同一步内对多个 tool_calls 用 asyncio.gather 并发，逐个套 超时→重试→熔断→失败语义（registry.run_tool）。
- 三条防幻觉不变量在 finalize 硬保证（不依赖模型自觉）：行情数字逐字不转写、知识库无有效证据即拒答、简报无素材固定话术。
- S3.3 注入纵深防御：act 产出经 security.wrap_external_content 定界符包裹+信任级标注（结构化隔离），
  finalize 的 _emit 出口经 security.audit_answer 检测泄漏/证据外 URL/指令执行迹象，命中替换安全话术并落库安全事件。
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
    PROMPT_GUARD_FILTER_UNTRUSTED,
)
from src.agent.intent import rule_classify
from src.agent.llm import llm
from src.agent.quote_service import looks_like_quote_query, precheck_quote_ambiguity
from src.agent.prompts import (
    AGENT_BUDGET_NOTE,
    AGENT_INTERNAL_RULES_PROMPT,
    AGENT_SYSTEM_PROMPT,
    NEWS_NO_EVIDENCE_ANSWER,
    REFUSAL_ANSWER,
    SECURITY_SAFE_ANSWER,
    SECURITY_SUSPICIOUS_NOTE,
)
from src.agent.registry import ToolResult, registry, run_tool
from src.agent.security import (
    TrustLevel,
    audit_answer,
    scan_instruction_patterns,
    strip_guard_boilerplate,
    wrap_external_content,
)
# 导入 tools 以触发工具向 registry 的注册（副作用）
import src.agent.tools  # noqa: F401  isort:skip
from src.agent.web_search import EMPTY_RESULT
from src.memory.memory_manager import memory_manager
from src.memory.schemas import SecurityEventRecord


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
    quote_confirm: dict | None            # 行情多标的歧义的确认载荷（候选标的，供 SSE confirm 事件）
    hard_tool_failed: bool                # hard 语义工具（kb 检索）失败 → 触发拒答
    budget_exceeded: bool                 # 步数/墙钟超限
    system_prompt: str                    # 本轮实际使用的系统提示（输出审计的泄漏比对基准）
    suspicious_trust: list[str]           # 检出注入样句的信任级（隔离层留痕，供警示与审计）
    security: dict                        # 输出审计结论（violations/intercepted 等，供 API 透出）
    answer: str
    answer_type: str
    intent: str
    assistant_message_id: str             # 本轮助手回答落库后的 memory 消息 ID（供反馈锚定）


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


def _wrapped_tool_message_content(
    name: str, res: ToolResult, kb_start: int
) -> tuple[str, list[str]]:
    """把工具结果转成给模型阅读的**结构化隔离数据块**（失败/降级给明确占位，供 replan 判断）。

    S3.3 第二层：证据不再裸文本入 prompt，改由显式定界符包裹 + 信任级标注 + 数据总纲声明；
    低信任源（web/tool）正文中的指令样句被剥离为占位符，命中清单随返回值上报供警示与审计；
    知识库切片属中信任源，正文逐字保留不剥离（守「数字与原文一致」不变量）。
    """
    spec = registry.get(name)
    trust = TrustLevel.parse(spec.trust_level if spec else None)
    if not res.ok:
        return f"工具 {name} 调用失败：{res.error or '未知错误'}", []
    if name == "search_knowledge":
        raw = _render_kb_block(res.data, kb_start)
    elif name == "search_realtime_quote":
        data = res.data or {}
        if data.get("confirm"):
            raw = "行情标的存在多个候选（歧义），未直接作答：" + (data.get("markdown") or "")
        elif not data.get("ok"):
            raw = "行情工具未能取数：" + (data.get("markdown") or "无数据")
        else:
            raw = data.get("markdown", "")
    else:
        # web_search 返回证据块字符串
        raw = res.data or EMPTY_RESULT
    # 仅对确会被剥离的低信任源扫描注入样句（中信任 KB 不剥离，不得虚报「已过滤」）
    hits = (
        scan_instruction_patterns(raw).hits
        if (trust.untrusted and PROMPT_GUARD_FILTER_UNTRUSTED)
        else []
    )
    return wrap_external_content(raw, trust, source_label=name), hits


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

    # 行情标的歧义前置预检：对原始问句先判是否跨公司歧义（不调行情工具、不取数），
    # 命中则短路到 finalize 直接弹选择框，避免 reason LLM 自行脑补具体标的先取数、浪费算力。
    quote_confirm = None
    if AGENT_LOOP_ENABLED and looks_like_quote_query(question):
        quote_confirm = precheck_quote_ambiguity(question)

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
        "quote_confirm": quote_confirm,
        "hard_tool_failed": False,
        "budget_exceeded": False,
        "system_prompt": AGENT_SYSTEM_PROMPT,
        "suspicious_trust": [],
        "security": {},
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
    quote_confirm = state.get("quote_confirm")
    hard_tool_failed = state["hard_tool_failed"]
    suspicious_trust = list(state.get("suspicious_trust") or [])

    for tc, res in zip(tool_calls, results):
        name = tc["name"]
        kb_start = len(sources) + 1
        # 累积结构化产物（供 finalize 组装与溯源）
        if res.ok and name == "search_knowledge":
            sources.extend(res.data)
        elif res.ok and name == "web_search":
            news_evidence.append(res.data)
        elif res.ok and name == "search_realtime_quote":
            data = res.data or {}
            if data.get("ok"):
                quote_markdown = data.get("markdown", "")
            elif data.get("confirm"):
                quote_confirm = data
        spec = registry.get(name)
        if not res.ok and spec is not None and spec.failure_class == "hard":
            hard_tool_failed = True

        content, hits = _wrapped_tool_message_content(name, res, kb_start)
        if hits and name not in suspicious_trust:
            # 隔离层留痕：记录检出注入样句的工具（同一工具多步命中去重），供终答警示与审计
            suspicious_trust.append(name)
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
        "quote_confirm": quote_confirm,
        "hard_tool_failed": hard_tool_failed,
        "suspicious_trust": suspicious_trust,
    }


def observe(state: AgentState) -> dict:
    """观察步：判定步数 / 墙钟 / token 预算是否耗尽，置 budget_exceeded 供路由与 finalize 使用。"""
    max_steps = AGENT_MAX_STEPS if AGENT_LOOP_ENABLED else 1
    elapsed = time.monotonic() - state["started_at"]
    exceeded = state["step"] >= max_steps or elapsed >= AGENT_LOOP_TIMEOUT_SECONDS
    return {"budget_exceeded": exceeded}


def route_observe(state: AgentState) -> str:
    # 行情多标的歧义确认：直接收敛到 finalize 推 confirm，不再让模型 replan 猜答（事前澄清优先）
    if state.get("quote_confirm") or state["hard_tool_failed"] or state["budget_exceeded"]:
        return "finalize"
    return "reason"


async def finalize(state: AgentState) -> dict:
    """终态组装：按三条防幻觉不变量决定 answer / answer_type / intent / sources，并落库本轮。"""
    trace = state["trace"]
    used = {e["name"] for e in trace if e.get("type") == "tool_result" and e.get("ok")}
    sources = state["sources"]
    quote_md = state["quote_markdown"]
    quote_confirm = state.get("quote_confirm")
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
    elif quote_confirm:
        # 行情多标的歧义确认：回固定澄清话术，候选经 SSE confirm 事件交前端内联点选（不含任何数字）
        return _emit(
            state, quote_confirm["markdown"], "confirm", "quote_query", [],
            quote_confirm=quote_confirm,
        )
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


def _emit(state: AgentState, answer: str, answer_type: str, intent: str, sources: list[dict], **extra) -> dict:
    """终答出口：先过 S3.3 输出审计（命中即替换安全话术并留痕），再落库本轮助手回答。

    审计置于落库之前，保证 memory 存档的也是终态文本；可疑注入警示只追加到模型生成类
    回答（拒答/固定话术/行情逐字文本不追加，守「quoted 不改写」不变量）。
    """
    security = _audit_final_answer(state, answer, answer_type, intent)
    if security["intercepted"]:
        answer = SECURITY_SAFE_ANSWER
    elif security["warn"]:
        answer = answer + SECURITY_SUSPICIOUS_NOTE
    assistant_message_id = ""
    if state["session_id"]:
        assistant_message_id = memory_manager.remember_assistant(
            state["session_id"], answer, answer_type, intent=intent
        ).message_id
    _persist_security_events(state, security, assistant_message_id, answer_type, intent)
    return {
        "answer": answer,
        "answer_type": answer_type,
        "intent": intent,
        "sources": sources,
        "assistant_message_id": assistant_message_id,
        "security": security["report"],
        **extra,
    }


# ---------- S3.3 第三层：输出审计 ----------

# 模型生成类作答：仅这些类型才追加可疑警示（固定话术与 quoted 逐字文本保持原形）
_GENERATED_TYPES = frozenset({"generated", "searched", "chatted", "agentic"})


def _evidence_text(state: AgentState) -> str:
    """汇总本轮全部入 prompt 的证据原文（kb 切片字段 + 网搜素材 + 行情 markdown），
    供审计判定回答中的 URL / 联系方式是否「证据中不存在」。"""
    parts: list[str] = []
    for hit in state["sources"]:
        parts.extend(
            str(hit.get(k) or "") for k in
            ("content", "original_text", "doc_name", "heading_path", "clause_position")
        )
    if state.get("quote_markdown"):
        parts.append(state["quote_markdown"])
    parts.extend(state.get("news_evidence") or [])
    # 排除隔离包裹样板（与 system prompt 总纲措辞同源，防自匹配误报）
    return strip_guard_boilerplate("\n".join(parts))


def _audit_final_answer(state: AgentState, answer: str, answer_type: str, intent: str) -> dict:
    """对终答跑规则审计 + 隔离层可疑判定，返回拦截/警示决策与违规清单。"""
    from src.agent.config import OUTPUT_AUDIT_ENABLED

    audit = audit_answer(
        answer,
        system_prompt=state.get("system_prompt") or AGENT_SYSTEM_PROMPT,
        leak_basis=AGENT_INTERNAL_RULES_PROMPT,
        evidence_text=_evidence_text(state),
        answer_type=answer_type,
    )
    violations = list(audit.violations)
    suspicious = bool(state.get("suspicious_trust"))
    if suspicious:
        violations.append({
            "rule": "input_injection",
            "detail": "、".join(state["suspicious_trust"]),
            "action": "warned",
        })
    # 可疑警示只加在模型生成类回答上；固定话术/quoted 保持原形
    warn = suspicious and not audit.intercepted and answer_type in _GENERATED_TYPES
    return {
        "intercepted": audit.intercepted,
        "warn": warn,
        "violations": violations,
        "report": {
            "checked": OUTPUT_AUDIT_ENABLED,
            "injection_detected": suspicious,
            "intercepted": audit.intercepted,
            "violations": violations,
            "suspicious_tools": state.get("suspicious_trust") or [],
        },
    }


def _persist_security_events(
    state: AgentState, security: dict, message_id: str, answer_type: str, intent: str
) -> None:
    """安全事件落库（SECURITY_EVENTS_PERSIST 可关）；落库失败不影响主链路，仅告警留痕。"""
    from src.agent.config import SECURITY_EVENTS_PERSIST

    if not SECURITY_EVENTS_PERSIST or not state["session_id"] or not security["violations"]:
        return
    try:
        for v in security["violations"]:
            memory_manager.record_security_event(SecurityEventRecord(
                event_id="", session_id=state["session_id"], message_id=message_id,
                rule=v["rule"],
                layer="isolation" if v["rule"] == "input_injection" else "output_audit",
                action=v["action"], detail=v.get("detail", ""),
                trust_level=(
                    "user" if v["rule"] == "input_injection"
                    else _trust_of_tool(v.get("detail", ""))
                ),
                answer_type=answer_type, intent=intent,
            ))
    except Exception as e:  # 审计留痕失败不阻断作答
        import logging

        logging.getLogger(__name__).warning("安全事件落库失败：%s", e)


def _trust_of_tool(detail: str) -> str | None:
    """隔离层留痕的 detail 为工具名串（如「web_search」），映射回信任级供事件检索；
    输出审计违规的 detail 为片段，在注册表查不到则返回 None。"""
    name = detail.split("、")[0].strip() if detail else ""
    spec = registry.get(name) if name else None
    return spec.trust_level if spec else None


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
        data = res.data or {}
        # 灰色确认：落库候选标的，供拒答/低置信回流分析「该补文档还是该调阈值」
        if data.get("confirm"):
            return {"confirm": True, "guessed": data.get("guessed"),
                    "candidates": data.get("candidates", [])}
        return data.get("data")
    return _truncate(res.data or "", 4000)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


# ---------- 图构建 ----------

def route_prepare(state: AgentState) -> str:
    """前置预检已锁定行情歧义 → 跳过 reason/act（不调工具、不取数），直接到 finalize 弹框。"""
    return "finalize" if state.get("quote_confirm") else "reason"


def build_agent_graph():
    """构建并编译带环的 Agent Loop 图（异步可 ainvoke / astream）。"""
    graph = StateGraph(AgentState)
    graph.add_node("prepare", prepare)
    graph.add_node("reason", reason)
    graph.add_node("act", act)
    graph.add_node("observe", observe)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", route_prepare, {"reason": "reason", "finalize": "finalize"})
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
        "quote_confirm": None,
        "hard_tool_failed": False,
        "budget_exceeded": False,
        "system_prompt": "",
        "suspicious_trust": [],
        "security": {},
        "answer": "",
        "answer_type": "",
        "intent": "",
        "assistant_message_id": "",
    }


# ---------- 对外入口（异步原生 + 同步桥接） ----------

async def aask_detail(question: str, session_id: str | None = None) -> dict:
    """返回 answer / intent / answer_type / sources / steps / security（供 HTTP 服务层调用）。"""
    state = await agent_graph.ainvoke(_initial_state(question, session_id), config=_RUN_CFG)
    return {
        "answer": state["answer"],
        "intent": state["intent"],
        "answer_type": state["answer_type"],
        "sources": state["sources"],
        "steps": state.get("trace", []),
        "message_id": state.get("assistant_message_id") or "",
        "confirm": state.get("quote_confirm"),
        "security": state.get("security") or {},
    }


def ask_detail(question: str, session_id: str | None = None) -> dict:
    return asyncio.run(aask_detail(question, session_id))


def ask(question: str, session_id: str | None = None) -> str:
    return asyncio.run(aask_detail(question, session_id))["answer"]


def _chunks(text: str, size: int = 48):
    for i in range(0, len(text), size):
        yield text[i:i + size]


async def aask_stream(question: str, session_id: str | None = None) -> AsyncIterator[dict]:
    """流式问答生成器：由 astream(updates) 驱动，产出 plan / step / meta / security / token 事件。

    与 ask_detail 走同一张图；plan/step/security 为过程事件（旧前端静默忽略），
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
                       "answer_type": delta["answer_type"], "sources": delta["sources"],
                       "message_id": delta.get("assistant_message_id") or "",
                       "security": delta.get("security") or {}}
                qc = delta.get("quote_confirm")
                if qc:
                    yield {"event": "confirm", "question": qc.get("question", ""),
                           "guessed": qc.get("guessed", ""), "candidates": qc.get("candidates", [])}
                security = delta.get("security") or {}
                if security.get("intercepted") or security.get("injection_detected"):
                    # 新增安全过程事件（旧前端静默忽略不影响渲染）；token 分片已是终态文本
                    yield {"event": "security",
                           "intercepted": security.get("intercepted", False),
                           "injection_detected": security.get("injection_detected", False),
                           "violations": security.get("violations", [])}
                for chunk in _chunks(delta["answer"]):
                    yield {"event": "token", "text": chunk}
