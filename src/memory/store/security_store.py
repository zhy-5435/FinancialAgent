"""提示词注入安全事件库（S3.3 审计闭环，纯增量）：把隔离/输出审计命中留痕落库为独立事件表

与运行时链路完全解耦——本表只增不删、不参与任何作答/拒答/拦截决策（拦截在 graph 内即时完成），
仅补齐安全审计与红队回流数据：
    - 哪类文档 / 信源反复出现注入样句 → 判定「该收紧该源隔离还是该补过滤模式」；
    - 输出审计命中分布（泄漏 / 证据外 URL / 指令执行）→ 回归观测拦截率与误报率。

存储位置：与会话记忆库 l1_memory.db 同库、独立表（security_events），与权威知识库物理隔离。
detail（命中片段）入库前复用 masking 做 PII 脱敏并截断，遵循 sqlite_store.py / feedback_store.py
的连接与幂等建表惯例。
"""
import sqlite3
import uuid

from src.config import MEMORY_DB_PATH
from src.memory.schemas import SecurityEventRecord

_SE_COLUMNS = [
    "event_id", "session_id", "message_id", "rule", "layer", "action",
    "detail", "trust_level", "answer_type", "intent", "created_at",
]

# 命中片段入库截断长度（防超大 blob，红队回流仅需可辨识前缀）
_DETAIL_MAX = 300


class SecurityEventStore:
    def __init__(self):
        self.db_path = str(MEMORY_DB_PATH)
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        """幂等建表：security_events（提示词注入安全事件），供审计闭环与红队回流。"""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute('''
        CREATE TABLE IF NOT EXISTS security_events (
            event_id    TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL DEFAULT '',
            message_id  TEXT NOT NULL DEFAULT '',
            rule        TEXT NOT NULL,
            layer       TEXT NOT NULL,
            action      TEXT NOT NULL,
            detail      TEXT,
            trust_level TEXT,
            answer_type TEXT,
            intent      TEXT,
            created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_security_rule_time
        ON security_events(rule, created_at)
        ''')
        cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_security_session
        ON security_events(session_id)
        ''')
        conn.commit()
        conn.close()

    def record(self, rec: SecurityEventRecord) -> SecurityEventRecord:
        """落库一条安全事件：自动补 event_id/created_at；detail 入库前 PII 脱敏 + 截断后就地回写。"""
        from src.memory.store.masking import mask_pii  # 局部导入，避免模块级环依赖

        if not rec.event_id:
            rec.event_id = f"sec-{uuid.uuid4().hex[:12]}"
        if rec.detail:
            rec.detail = mask_pii(rec.detail)[:_DETAIL_MAX]
        if not rec.created_at:
            rec.created_at = _now()

        conn = self._get_conn()
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO security_events "
                f"({','.join(_SE_COLUMNS)}) VALUES ({','.join(['?'] * len(_SE_COLUMNS))})",
                (
                    rec.event_id, rec.session_id, rec.message_id, rec.rule, rec.layer,
                    rec.action, rec.detail, rec.trust_level, rec.answer_type, rec.intent,
                    rec.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return rec

    def list_for_analysis(
        self,
        rule: str | None = None,
        session_id: str | None = None,
        limit: int = 200,
    ) -> list[SecurityEventRecord]:
        """按规则/会话筛选最近的安全事件，供审计回流与红队回归分析。"""
        conn = self._get_conn()
        try:
            where, params = [], []
            if rule:
                where.append("rule = ?")
                params.append(rule)
            if session_id:
                where.append("session_id = ?")
                params.append(session_id)
            clause = f"WHERE {' AND '.join(where)}" if where else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT {', '.join(_SE_COLUMNS)} FROM security_events "
                f"{clause} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [
            SecurityEventRecord(
                event_id=r[0], session_id=r[1], message_id=r[2], rule=r[3], layer=r[4],
                action=r[5], detail=r[6], trust_level=r[7], answer_type=r[8], intent=r[9],
                created_at=r[10],
            )
            for r in rows
        ]


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 全局实例（懒初始化，首次访问建表）
security_store = SecurityEventStore()
