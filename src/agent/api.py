"""FastAPI 问答服务（L3 Agent 应用层）
将 LangGraph 问答流程（src/agent/graph.py）封装为 REST 接口，供 Web 前端调用。
出参含回答文本、作答类型（generated/refused）与溯源切片列表；
session_id / message_id 字段先行占位，后续升级多轮会话与流式输出不改接口形状。

启动方式（项目根目录执行，需先启动 L2 检索服务 :8000）：
    python -m uvicorn src.agent.api:app --host 0.0.0.0 --port 8001
    或
    python -m src.agent.api
"""
import json
import time
import uuid

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.agent.config import AGENT_CORS_ORIGINS, SEARCH_API_BASE
from src.agent.graph import aask_detail, aask_stream
from src.agent.schemas import (
    ChatRequest,
    ChatResponse,
    RestoredMessage,
    SessionItem,
    SessionMessagesResponse,
)
from src.memory.memory_manager import memory_manager


# ---------- 应用实例 ----------

app = FastAPI(
    title="L3 金融助手问答服务",
    description=(
        "L3 Agent 应用层：LangGraph 编排「统一 Agent Loop（plan → act 并发工具 → observe → "
        "replan | answer）」流程的 REST 接口；LLM 经 bind_tools 自主选择知识库检索/联网资讯/实时行情工具，"
        "受步数与墙钟预算约束；知识库分支强制溯源、无有效证据拒答，行情数字逐字不转写，"
        "供 Web 前端与上层应用调用。"
    ),
    version="2.0.0",
)

if AGENT_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=AGENT_CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health", summary="健康检查")
def health():
    """探活接口：返回本身状态与 L2 检索服务连通性"""
    l2_ok = False
    try:
        l2_ok = httpx.get(f"{SEARCH_API_BASE}/health", timeout=5).status_code == 200
    except Exception:
        pass
    return {
        "status": "ok",
        "search_api_base": SEARCH_API_BASE,
        "search_api_reachable": l2_ok,
    }


@app.post("/chat", response_model=ChatResponse, summary="Agent Loop 问答")
async def chat(req: ChatRequest):
    """
    统一 Agent Loop（plan → act 并发工具 → observe → replan | answer）问答入口：
    LLM 经 bind_tools 自主选择工具（知识库检索 / 联网资讯 / 实时行情），受步数与墙钟预算约束；
    知识库结论强制溯源、无有效证据拒答、行情数字逐字不转写。intent 由实际调用的工具回溯派生。
    """
    session_id = req.session_id or f"sess-{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()
    try:
        result = await aask_detail(req.question, session_id)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        raise HTTPException(
            status_code=502,
            detail=f"L2 检索服务不可用（{SEARCH_API_BASE}），请先启动 python -m uvicorn src.search.api:app：{e}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"问答服务内部错误：{e}")

    return ChatResponse(
        session_id=session_id,
        message_id=f"msg-{uuid.uuid4().hex[:12]}",
        question=req.question,
        answer=result["answer"],
        intent=result["intent"],
        answer_type=result["answer_type"],
        sources=result["sources"],
        steps=result.get("steps"),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _sse(event: str, data: dict) -> str:
    """编码单条 SSE 帧（event + data 双字段，JSON 负载）"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat/stream", summary="Agent Loop 问答（SSE 流式）")
async def chat_stream(req: ChatRequest):
    """
    与 POST /chat 同一 Agent Loop 链路，按 SSE 事件流输出（供前端 fetch + ReadableStream 消费）：
        plan   规划步（LLM 决定调用哪些工具）：step / text / tools（新增过程事件，旧前端可忽略）
        step   单个工具执行结果：name / ok / summary（新增过程事件）
        meta   终答就绪即推送：intent + answer_type + sources + session_id/message_id
        token  终答文本分片（拒答/行情/无素材为定稿文本分片）
        done   正常结束：elapsed_ms
        error  链路异常：detail（如 L2 不可用，对应 502 语义）
    """
    session_id = req.session_id or f"sess-{uuid.uuid4().hex[:12]}"
    message_id = f"msg-{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()

    async def gen():
        try:
            async for ev in aask_stream(req.question, session_id):
                etype = ev["event"]
                if etype == "meta":
                    yield _sse("meta", {
                        "session_id": session_id,
                        "message_id": message_id,
                        "question": req.question,
                        "intent": ev["intent"],
                        "answer_type": ev["answer_type"],
                        "sources": ev["sources"],
                    })
                elif etype == "token":
                    yield _sse("token", {"text": ev["text"]})
                elif etype == "plan":
                    yield _sse("plan", {"step": ev["step"], "text": ev["text"], "tools": ev["tools"]})
                elif etype == "step":
                    yield _sse("step", {"name": ev["name"], "ok": ev["ok"], "summary": ev["summary"]})
            yield _sse("done", {"elapsed_ms": int((time.perf_counter() - started) * 1000)})
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
            yield _sse("error", {"status": 502, "detail": (
                f"L2 检索服务不可用（{SEARCH_API_BASE}），请先启动 python -m uvicorn src.search.api:app：{e}"
            )})
        except Exception as e:
            yield _sse("error", {"status": 500, "detail": f"问答服务内部错误：{e}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx 等反代关闭缓冲，保证逐 token 到达（本地 Vite proxy 无此问题）
            "X-Accel-Buffering": "no",
        },
    )


# ---------- 会话历史恢复（多轮持久化） ----------

@app.get("/sessions", response_model=list[SessionItem], summary="会话列表")
def list_sessions(limit: int = Query(50, ge=1, le=200, description="返回会话数上限")):
    """按最近活跃降序返回已持久化的会话列表，供前端侧栏历史恢复入口"""
    return [
        SessionItem(
            session_id=s.session_id, title=s.title,
            updated_at=s.updated_at, message_count=s.message_count,
        )
        for s in memory_manager.list_sessions(limit=limit)
    ]


@app.get(
    "/sessions/{session_id}/messages",
    response_model=SessionMessagesResponse,
    summary="会话历史消息",
)
def get_session_messages(
    session_id: str,
    limit: int | None = Query(None, ge=1, le=500, description="仅返回最近 N 条（缺省全部）"),
):
    """返回指定会话的可见历史消息（仅 user/assistant，按时间升序），供多轮回放"""
    restored = [
        RestoredMessage(
            message_id=m.message_id, role=m.role, content=m.content,
            intent=m.intent, answer_type=m.answer_type, created_at=m.created_at,
        )
        for m in memory_manager.get_messages(session_id, limit=limit)
        if m.role in ("user", "assistant")
    ]
    return SessionMessagesResponse(session_id=session_id, messages=restored)


@app.delete("/sessions/{session_id}", summary="删除会话")
def delete_session(session_id: str):
    """删除会话的消息与压缩日志（原始审计仅在显式删除时移除）"""
    memory_manager.delete_session(session_id)
    return {"session_id": session_id, "deleted": True}


if __name__ == "__main__":
    import uvicorn

    from src.agent.config import AGENT_API_HOST, AGENT_API_PORT

    uvicorn.run(app, host=AGENT_API_HOST, port=AGENT_API_PORT)
