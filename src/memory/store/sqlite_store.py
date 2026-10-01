"""SQLite 会话记忆库：原始完整消息序列（审计存档，永不删除）+ 压缩元数据日志

与权威知识库 l1_core.db 物理隔离。遵循 src/data/sqlite_client.py 的连接与幂等建表惯例。
本层只负责持久化，不依赖上下文引擎：token_est 由上层（memory_manager）填充后原样存储。
"""
import json
import sqlite3
import uuid

from src.config import MEMORY_DB_PATH
from src.memory.schemas import (
    CompressionRecord,
    PriorityTag,
    SessionSummary,
    StoredMessage,
)

_MSG_COLUMNS = [
    "message_id", "session_id", "seq", "role", "content",
    "intent", "answer_type", "priority_tag", "token_est",
    "tool_name", "tool_payload", "compression_state", "created_at",
]


class ConversationStore:
    def __init__(self):
        self.db_path = str(MEMORY_DB_PATH)
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_tables(self):
        """幂等建表：conversation_message（原始序列）与 compression_log（压缩元数据）"""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute('''
        CREATE TABLE IF NOT EXISTS conversation_message (
            message_id        TEXT PRIMARY KEY,
            session_id        TEXT NOT NULL,
            seq               INTEGER NOT NULL,
            role              TEXT NOT NULL,
            content           TEXT NOT NULL,
            intent            TEXT,
            answer_type       TEXT,
            priority_tag      INTEGER NOT NULL DEFAULT 3,
            token_est         INTEGER NOT NULL DEFAULT 0,
            tool_name         TEXT,
            tool_payload      TEXT,
            compression_state TEXT NOT NULL DEFAULT 'active',
            created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_msg_session_seq
        ON conversation_message(session_id, seq)
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS compression_log (
            record_id            TEXT PRIMARY KEY,
            session_id           TEXT NOT NULL,
            scope                TEXT NOT NULL,
            version              INTEGER NOT NULL,
            covered_message_ids  TEXT NOT NULL DEFAULT '[]',
            summary_text         TEXT NOT NULL,
            summary_token_est    INTEGER NOT NULL DEFAULT 0,
            key_entities         TEXT NOT NULL DEFAULT '[]',
            created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_compression_session
        ON compression_log(session_id, version)
        ''')
        conn.commit()
        conn.close()

    # ---------- 原始消息序列 ----------

    def next_seq(self, session_id: str) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT MAX(seq) FROM conversation_message WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return (row[0] + 1) if row and row[0] is not None else 1
        finally:
            conn.close()

    def append_message(self, msg: StoredMessage) -> StoredMessage:
        """落库一条消息：入库前 PII 脱敏、自动补 seq；就地回写 seq/created_at 后返回。

        priority_tag 存整数等级（供排序淘汰），compression_state 默认 active。
        """
        from src.memory.store.masking import mask_pii  # 局部导入，避免模块级环依赖

        if not msg.message_id:
            msg.message_id = f"msg-{uuid.uuid4().hex[:12]}"
        if not msg.seq:
            msg.seq = self.next_seq(msg.session_id)

        conn = self._get_conn()
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO conversation_message "
                f"({','.join(_MSG_COLUMNS)}) VALUES ({','.join(['?'] * len(_MSG_COLUMNS))})",
                (
                    msg.message_id, msg.session_id, msg.seq, msg.role,
                    mask_pii(msg.content), msg.intent, msg.answer_type,
                    int(msg.priority_tag), msg.token_est, msg.tool_name,
                    msg.tool_payload, msg.compression_state,
                    msg.created_at or _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return msg

    def load_messages(
        self, session_id: str, before_seq: int | None = None, limit: int | None = None
    ) -> list[StoredMessage]:
        """按 seq 升序读取会话消息；before_seq 取该序号之前，limit 取最近 N 条（仍按升序返回）"""
        conn = self._get_conn()
        try:
            where = ["session_id = ?"]
            params: list = [session_id]
            if before_seq is not None:
                where.append("seq < ?")
                params.append(before_seq)
            sql = f"SELECT {_msg_select()} FROM conversation_message WHERE {' AND '.join(where)} ORDER BY seq ASC"
            if limit is not None:
                # 取最近 N 条：先按 seq 倒序限量，再整体升序
                sql = sql.replace("ORDER BY seq ASC", "ORDER BY seq DESC LIMIT ?")
                params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        msgs = [self._row_to_msg(r) for r in rows]
        if limit is not None:
            msgs.reverse()
        return msgs

    def mark_compression_state(self, message_ids: list[str], state: str):
        """将被摘要覆盖的消息标记为 summarized/replaced（原始行永不删除）"""
        if not message_ids:
            return
        conn = self._get_conn()
        try:
            conn.executemany(
                "UPDATE conversation_message SET compression_state = ? WHERE message_id = ?",
                [(state, mid) for mid in message_ids],
            )
            conn.commit()
        finally:
            conn.close()

    # ---------- 会话列表（历史恢复）----------

    def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT m.session_id,
                       COALESCE(
                         (SELECT c.content FROM conversation_message c
                          WHERE c.session_id = m.session_id AND c.role = 'user'
                          ORDER BY c.seq ASC LIMIT 1), ''),
                       MAX(m.created_at),
                       COUNT(*)
                FROM conversation_message m
                GROUP BY m.session_id
                ORDER BY MAX(m.created_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [
            SessionSummary(
                session_id=sid,
                title=(title or "")[:60],
                updated_at=updated_at,
                message_count=count,
            )
            for sid, title, updated_at, count in rows
        ]

    def delete_session(self, session_id: str):
        """删除整个会话的消息与压缩日志（历史恢复场景的清理；原始审计仅在显式删除时移除）"""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM conversation_message WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM compression_log WHERE session_id = ?", (session_id,))
            conn.commit()
        finally:
            conn.close()

    # ---------- 压缩元数据日志 ----------

    def next_compression_version(self, session_id: str) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT MAX(version) FROM compression_log WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return (row[0] + 1) if row and row[0] is not None else 1
        finally:
            conn.close()

    def record_compression(self, rec: CompressionRecord) -> CompressionRecord:
        """登记一条压缩摘要：自动补 record_id/version，序列化 covered_message_ids/key_entities"""
        if not rec.record_id:
            rec.record_id = f"cmp-{uuid.uuid4().hex[:12]}"
        if not rec.version:
            rec.version = self.next_compression_version(rec.session_id)
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO compression_log "
                "(record_id, session_id, scope, version, covered_message_ids, "
                " summary_text, summary_token_est, key_entities, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    rec.record_id, rec.session_id, rec.scope, rec.version,
                    json.dumps(rec.covered_message_ids, ensure_ascii=False),
                    rec.summary_text, rec.summary_token_est,
                    json.dumps(rec.key_entities, ensure_ascii=False),
                    rec.created_at or _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return rec

    def latest_compressions(self, session_id: str, limit: int = 5) -> list[CompressionRecord]:
        """取会话最近的压缩摘要（按 version 倒序），供全局摘要档复用"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT record_id, session_id, scope, version, covered_message_ids, "
                "summary_text, summary_token_est, key_entities, created_at "
                "FROM compression_log WHERE session_id = ? ORDER BY version DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            CompressionRecord(
                record_id=r[0], session_id=r[1], scope=r[2], version=r[3],
                covered_message_ids=json.loads(r[4] or "[]"),
                summary_text=r[5], summary_token_est=r[6],
                key_entities=json.loads(r[7] or "[]"),
                created_at=r[8],
            )
            for r in rows
        ]

    @staticmethod
    def _row_to_msg(r: tuple) -> StoredMessage:
        return StoredMessage(
            message_id=r[0], session_id=r[1], seq=r[2], role=r[3], content=r[4],
            intent=r[5], answer_type=r[6], priority_tag=PriorityTag(r[7]),
            token_est=r[8], tool_name=r[9], tool_payload=r[10],
            compression_state=r[11], created_at=r[12],
        )


def _msg_select() -> str:
    return ", ".join(_MSG_COLUMNS)


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 全局实例（懒初始化，首次访问建表）
conversation_store = ConversationStore()
