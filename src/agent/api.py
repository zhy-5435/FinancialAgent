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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.agent.config import AGENT_CORS_ORIGINS, SEARCH_API_BASE
from src.agent.graph import ask_detail, ask_stream
from src.agent.schemas import ChatRequest, ChatResponse


# ---------- 应用实例 ----------

app = FastAPI(
    title="L3 金融助手问答服务",
    description=(
        "L3 Agent 应用层：LangGraph 编排「意图识别 → 四路分发（知识库问答/闲聊/财经资讯/实时行情）」 "
        "流程的 REST 接口；知识库分支回答仅基于 L1 有效切片并强制溯源，"
        "低置信意图回落知识库分支，供 Web 前端与上层应用调用。"
    ),
    version="1.1.0",
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


@app.post("/chat", response_model=ChatResponse, summary="意图路由问答")
def chat(req: ChatRequest):
    """
    输入问题，先走意图识别（低置信回落 kb_qa），再按分支作答：
    kb_qa 走「L2 检索 → 相似度阈值过滤 → 约束作答/拒答」；chitchat 走通用对话；
    news_search / quote_query 为占位分支（M3/M4 接入工具后生效）。
    """
    session_id = req.session_id or f"sess-{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()
    try:
        result = ask_detail(req.question)
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
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _sse(event: str, data: dict) -> str:
    """编码单条 SSE 帧（event + data 双字段，JSON 负载）"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat/stream", summary="意图路由问答（SSE 流式）")
def chat_stream(req: ChatRequest):
    """
    与 POST /chat 同一问答链路，按 SSE 事件流输出（供前端 fetch + ReadableStream 消费）：
        meta   分支就绪后即推送：intent + answer_type + sources + session_id/message_id（前端可先渲染溯源面板）
        token  LLM 增量文本（拒答/占位分支为单条固定话术）
        done   正常结束：elapsed_ms
        error  链路异常：detail（如 L2 不可用，对应 502 语义）
    """
    session_id = req.session_id or f"sess-{uuid.uuid4().hex[:12]}"
    message_id = f"msg-{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()

    def gen():
        try:
            for ev in ask_stream(req.question):
                if ev["event"] == "meta":
                    yield _sse("meta", {
                        "session_id": session_id,
                        "message_id": message_id,
                        "question": req.question,
                        "intent": ev["intent"],
                        "answer_type": ev["answer_type"],
                        "sources": ev["sources"],
                    })
                else:
                    yield _sse("token", {"text": ev["text"]})
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


if __name__ == "__main__":
    import uvicorn

    from src.agent.config import AGENT_API_HOST, AGENT_API_PORT

    uvicorn.run(app, host=AGENT_API_HOST, port=AGENT_API_PORT)
