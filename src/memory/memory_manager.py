"""会话记忆门面：对 L3 graph/api 暴露「落库本轮 + 构建上下文 + 历史恢复」的高层入口

职责边界：
    - 只读/写持久化层（store），只调上下文引擎（context），不被 store/context 反向依赖；
    - 上层（graph 的 LLM 分支作答前）调 build() 取精简上下文；结果产出后调 remember_* 落库。
"""
import json

from src.memory.context.builder import ContextBuilder
from src.memory.context.tokenizer import estimate
from src.memory.schemas import ContextBundle, SessionSummary, StoredMessage
from src.memory.store.sqlite_store import conversation_store


class MemoryManager:
    def __init__(self, store=conversation_store, builder: ContextBuilder | None = None):
        self.store = store
        self.builder = builder or ContextBuilder()

    # ---------- 构建推理上下文（调用模型前） ----------

    def build(
        self,
        session_id: str,
        base_system_prompt: str,
        question: str,
        *,
        mode: str = "turns",
        extra_tokens_estimate: int = 0,
        persist_summary: bool = True,
        before_seq: int | None = None,
    ) -> ContextBundle:
        """加载会话历史 → 上下文引擎按档压缩 → 返回可直接 invoke 的消息视图。

        extra_tokens_estimate：本轮「新鲜内容」（如 kb 检索证据、news 网搜素材）的预估占用，
        纳入预算档位判断但不参与历史压缩。
        before_seq：仅加载该序号**之前**的历史（本轮用户消息已先落库时，用它排除本轮）。
        """
        history = self.store.load_messages(session_id, before_seq=before_seq)
        return self.builder.build_context(
            session_id,
            base_system_prompt,
            question,
            history,
            mode=mode,
            extra_tokens_estimate=extra_tokens_estimate,
            persist_summary=persist_summary,
        )

    # ---------- 落库本轮（审计存档，永不删除） ----------

    def remember_user(self, session_id: str, content: str, intent: str | None = None) -> StoredMessage:
        return self._append(
            session_id=session_id, role="user", content=content, intent=intent
        )

    def remember_assistant(
        self, session_id: str, content: str, answer_type: str, intent: str | None = None
    ) -> StoredMessage:
        return self._append(
            session_id=session_id, role="assistant", content=content,
            intent=intent, answer_type=answer_type,
        )

    def remember_tool(
        self, session_id: str, tool_name: str, content: str, payload: object | None = None
    ) -> StoredMessage:
        """落库工具结果：payload 存结构化 JSON（供上下文引擎精简渲染），content 存可读摘要文本"""
        tool_payload = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        return self._append(
            session_id=session_id, role="tool", content=content,
            tool_name=tool_name, tool_payload=tool_payload,
        )

    def _append(self, **kwargs) -> StoredMessage:
        content = kwargs.get("content", "")
        msg = StoredMessage(
            message_id=kwargs.pop("message_id", "") or _gen_msg_id(),
            session_id=kwargs["session_id"],
            seq=0,  # 交由 store 自动分配
            role=kwargs["role"],
            content=content,
            intent=kwargs.get("intent"),
            answer_type=kwargs.get("answer_type"),
            tool_name=kwargs.get("tool_name"),
            tool_payload=kwargs.get("tool_payload"),
            token_est=estimate(content),
        )
        return self.store.append_message(msg)

    # ---------- 历史恢复 ----------

    def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        return self.store.list_sessions(limit=limit)

    def get_messages(self, session_id: str, limit: int | None = None) -> list[StoredMessage]:
        return self.store.load_messages(session_id, limit=limit)

    def delete_session(self, session_id: str):
        self.store.delete_session(session_id)


def _gen_msg_id() -> str:
    import uuid

    return f"msg-{uuid.uuid4().hex[:12]}"


# 全局单例
memory_manager = MemoryManager()
