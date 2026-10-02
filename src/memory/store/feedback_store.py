"""回答反馈标注库（HITL 反馈闭环，纯增量）：把用户对回答的「有用/无用/内容纠错」落库为独立标注表

与运行时链路完全解耦——本表只增不删、不参与任何作答/拒答决策，仅补齐评测/校准数据：
    - 拒答（refused）与低置信 generated 案例回流 → 判定「该补文档」还是「该调阈值」；
    - answer_type/intent/question 由上层（memory_manager）从被反馈的助手消息服务端回填后原样存储，
      本层不反向依赖上下文引擎；comment（纠错说明）入库前复用 masking 做 PII 脱敏。

存储位置：与会话记忆库 l1_memory.db 同库、独立表（answer_feedback），与权威知识库物理隔离。
遵循 src/data/sqlite_client.py 与 sqlite_store.py 的连接与幂等建表惯例。
"""
import sqlite3
import uuid

from src.config import MEMORY_DB_PATH
from src.memory.schemas import FeedbackRecord

_FB_COLUMNS = [
    "feedback_id", "session_id", "message_id", "category", "comment",
    "answer_type", "intent", "question", "created_at",
]


class FeedbackStore:
    def __init__(self):
        self.db_path = str(MEMORY_DB_PATH)
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        """幂等建表：answer_feedback（回答反馈标注），供离线回流分析"""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute('''
        CREATE TABLE IF NOT EXISTS answer_feedback (
            feedback_id  TEXT PRIMARY KEY,
            session_id   TEXT NOT NULL,
            message_id   TEXT NOT NULL,
            category     TEXT NOT NULL,
            comment      TEXT,
            answer_type  TEXT,
            intent       TEXT,
            question     TEXT,
            created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_feedback_message
        ON answer_feedback(message_id)
        ''')
        cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_feedback_category
        ON answer_feedback(category, answer_type)
        ''')
        conn.commit()
        conn.close()

    def record(self, rec: FeedbackRecord) -> FeedbackRecord:
        """落库一条反馈：自动补 feedback_id/created_at；comment 入库前 PII 脱敏后就地回写。"""
        from src.memory.store.masking import mask_pii  # 局部导入，避免模块级环依赖

        if not rec.feedback_id:
            rec.feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
        if rec.comment:
            rec.comment = mask_pii(rec.comment)
        if not rec.created_at:
            rec.created_at = _now()

        conn = self._get_conn()
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO answer_feedback "
                f"({','.join(_FB_COLUMNS)}) VALUES ({','.join(['?'] * len(_FB_COLUMNS))})",
                (
                    rec.feedback_id, rec.session_id, rec.message_id, rec.category,
                    rec.comment, rec.answer_type, rec.intent, rec.question, rec.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return rec

    def list_for_analysis(
        self,
        category: str | None = None,
        answer_type: str | None = None,
        limit: int = 200,
    ) -> list[FeedbackRecord]:
        """按类别/作答类型筛选最近的标注，供离线校准（拒答 vs 低置信回流分析）。"""
        conn = self._get_conn()
        try:
            where, params = [], []
            if category:
                where.append("category = ?")
                params.append(category)
            if answer_type:
                where.append("answer_type = ?")
                params.append(answer_type)
            clause = f"WHERE {' AND '.join(where)}" if where else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT {', '.join(_FB_COLUMNS)} FROM answer_feedback "
                f"{clause} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [
            FeedbackRecord(
                feedback_id=r[0], session_id=r[1], message_id=r[2], category=r[3],
                comment=r[4], answer_type=r[5], intent=r[6], question=r[7], created_at=r[8],
            )
            for r in rows
        ]

    def exists_for_message(self, message_id: str) -> bool:
        """该回答是否已有反馈标注（供前端回显已提交状态、避免重复计入）。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM answer_feedback WHERE message_id = ? LIMIT 1",
                (message_id,),
            ).fetchone()
        finally:
            conn.close()
        return row is not None


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 全局实例（懒初始化，首次访问建表）
feedback_store = FeedbackStore()
