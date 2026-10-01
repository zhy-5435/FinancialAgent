"""SQLite 权威主库：建表 / Excel 清洗导入 / 值域校验 / 原始文档路径对账 / 切片详情查询 / FTS5 关键词检索"""
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

from src.config import EXCEL_PATH, RAW_DATA_DIR, SQLITE_DB_PATH

# FTS5 关键词检索虚拟表（knowledge_id 不参与全文匹配，仅作关联主表外键）
FTS_TABLE = "fts_l1_knowledge"
# 检索用分词：中文按单字切分，英文/数字（含小数点、百分号，如 2.4.2 / 20%）连续串作整词
_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z._%]+|[\u4e00-\u9fff]")


def tokenize_for_fts(text: str) -> list[str]:
    """FTS5 检索用分词：中文单字 + 英文数字整词，建索引与查询两侧共用同一套规则"""
    return _TOKEN_PATTERN.findall(text or "")


def parse_keywords(raw) -> list:
    """keywords 在 SQLite 中为 JSON 字符串，解析为列表便于上层直接使用"""
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []

# 与《表结构设计》建表语句保持一致的列顺序（Excel 导入、SQL 查询均按此对齐）
DOC_COLUMNS = [
    "doc_version_id", "doc_id", "doc_name", "version", "doc_type",
    "publish_dept", "publish_date", "effective_date", "expire_date",
    "status", "file_path", "created_at", "updated_at",
]
CHUNK_COLUMNS = [
    "knowledge_id", "doc_version_id", "clause_position", "heading_path",
    "chunk_type", "content", "original_text", "keywords", "status",
]


def normalize_filename(name: str) -> str:
    """文件名规范化：去除全部空白字符、统一各类中点为半角中点，用于路径模糊匹配"""
    name = "".join(str(name).split())
    for dot in ("\u30fb", "\u2027", "\u2022"):  # 片假名中点/音节分隔符/圆点
        name = name.replace(dot, "\u00b7")
    return name


class SqliteClient:
    # 与表结构 CHECK 约束一致的合法值域，导入前先校验，Excel 数据变化时尽早暴露问题
    DOC_STATUS_VALUES = ("draft", "valid", "superseded", "expired", "archived")
    CHUNK_STATUS_VALUES = ("valid", "expired", "deleted", "need_review")
    CHUNK_TYPE_VALUES = (
        "clause", "table", "exception", "principle", "definition",
        "product_element", "fee", "risk", "process", "other",
    )

    def __init__(self):
        self.db_path = str(SQLITE_DB_PATH)
        # FTS5 不可用时关键词检索路自动退化，上层退回纯向量检索
        self.fts_available = True
        self._init_tables()
        self._ensure_fts_index()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")  # SQLite 外键约束需在每个连接上单独开启
        return conn

    def _init_tables(self):
        """按《表结构设计》初始化 doc_version / knowledge_chunk，幂等操作"""
        conn = self._get_conn()
        cursor = conn.cursor()

        # 清理旧版结构的表（先子表后父表）；本项目以 Excel 为唯一数据源全量重建，旧结构数据不再保留
        cursor.execute("DROP TABLE IF EXISTS knowledge_point")
        cursor.execute("DROP TABLE IF EXISTS document_meta")

        # 表1：文档版本表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS doc_version (
            doc_version_id  TEXT PRIMARY KEY,           -- 如 REG-001:V2026
            doc_id          TEXT NOT NULL,              -- 如 REG-001
            doc_name        TEXT NOT NULL,
            version         TEXT NOT NULL,              -- 如 V2026
            doc_type        TEXT,                       -- 监管规则 / 内部制度 / 产品说明书
            publish_dept    TEXT,
            publish_date    TEXT,                       -- YYYY-MM-DD
            effective_date  TEXT,                       -- YYYY-MM-DD
            expire_date     TEXT,                       -- YYYY-MM-DD，NULL 表示长期有效
            status          TEXT NOT NULL DEFAULT 'valid'
                CHECK (status IN ('draft','valid','superseded','expired','archived')),
            file_path       TEXT,
            created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (doc_id, version)
        )
        ''')

        # 表2：知识切片表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_chunk (
            knowledge_id    TEXT PRIMARY KEY,           -- 如 L1-0001
            doc_version_id  TEXT NOT NULL,              -- 关联 doc_version.doc_version_id
            clause_position TEXT,                       -- 如 2.4.2，产品说明书可为 NULL
            heading_path    TEXT,                       -- 如 第二章 交易时间 > 2.4 交易时间 > 2.4.2
            chunk_type      TEXT NOT NULL DEFAULT 'clause'
                CHECK (chunk_type IN (
                    'clause','table','exception','principle','definition',
                    'product_element','fee','risk','process','other'
                )),
            content         TEXT NOT NULL,              -- 检索用文本
            original_text   TEXT NOT NULL,              -- 原文逐字摘录
            keywords        TEXT,                       -- JSON 字符串，如 ["科创板","20%"]
            status          TEXT NOT NULL DEFAULT 'valid'
                CHECK (status IN ('valid','expired','deleted','need_review')),
            FOREIGN KEY (doc_version_id) REFERENCES doc_version(doc_version_id)
        )
        ''')

        # 索引
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_doc_version_status
        ON doc_version(status, effective_date, expire_date)
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_chunk_doc_version
        ON knowledge_chunk(doc_version_id, status)
        ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_chunk_type
        ON knowledge_chunk(chunk_type)
        ''')

        # FTS5 关键词索引：仅索引 status=valid 切片（与向量路同步范围一致），随导入全量重建
        try:
            cursor.execute(f'''
            CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
                knowledge_id UNINDEXED,
                body
            )
            ''')
        except sqlite3.OperationalError as e:
            self.fts_available = False
            print(f"[警告] 当前 SQLite 不支持 FTS5，关键词检索路不可用，将退化为纯向量检索：{e}")

        conn.commit()
        conn.close()

    # ---------- Excel 数据清洗 ----------

    @staticmethod
    def _strip_text(df: pd.DataFrame, cols: list[str]) -> None:
        """文本列去除首尾空白（Excel 单元格常见换行/空格噪声，如 'valid\\n'）"""
        for col in cols:
            df[col] = df[col].map(lambda v: v.strip() if isinstance(v, str) else v)

    def _clean_doc_version(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.dropna(subset=["doc_version_id"]).copy()
        self._strip_text(df, [
            "doc_version_id", "doc_id", "doc_name", "version",
            "doc_type", "publish_dept", "status", "file_path",
        ])
        # status 缺失时按表结构 DEFAULT 'valid' 语义补齐
        df["status"] = df["status"].fillna("valid").replace("", "valid")
        # 日期/时间列统一转字符串（SQLite 不接受 pandas Timestamp 类型）
        for col in ("publish_date", "effective_date", "expire_date"):
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
        for col in ("created_at", "updated_at"):
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        return df[DOC_COLUMNS]

    def _clean_knowledge_chunk(self, df: pd.DataFrame) -> pd.DataFrame:
        # 过滤 Excel 中残留的整行空行（knowledge_id 为空的行全部丢弃）
        df = df.dropna(subset=["knowledge_id"]).copy()
        self._strip_text(df, [
            "knowledge_id", "doc_version_id", "clause_position", "heading_path",
            "chunk_type", "content", "original_text", "keywords", "status",
        ])
        df["status"] = df["status"].fillna("valid").replace("", "valid")
        # clause_position（产品说明书无条款号）、keywords 允许为空，NaN 统一转 None 存 NULL
        for col in ("clause_position", "keywords"):
            df[col] = df[col].where(df[col].notna(), None)
        return df[CHUNK_COLUMNS]

    def _validate(self, df_doc: pd.DataFrame, df_chunk: pd.DataFrame) -> None:
        """导入前校验值域与引用完整性，不合法直接报错，避免脏数据悄悄进库"""
        bad = df_doc[~df_doc["status"].isin(self.DOC_STATUS_VALUES)]
        if not bad.empty:
            raise ValueError(f"doc_version.status 存在非法值：{bad['status'].unique().tolist()}")
        bad = df_chunk[~df_chunk["chunk_type"].isin(self.CHUNK_TYPE_VALUES)]
        if not bad.empty:
            raise ValueError(f"knowledge_chunk.chunk_type 存在非法值：{bad['chunk_type'].unique().tolist()}")
        bad = df_chunk[~df_chunk["status"].isin(self.CHUNK_STATUS_VALUES)]
        if not bad.empty:
            raise ValueError(f"knowledge_chunk.status 存在非法值：{bad['status'].unique().tolist()}")
        unknown = set(df_chunk["doc_version_id"]) - set(df_doc["doc_version_id"])
        if unknown:
            raise ValueError(f"knowledge_chunk 引用了未登记的 doc_version_id：{sorted(unknown)}")

    # ---------- FTS5 关键词索引 ----------

    @staticmethod
    def _fts_body(content: str, keywords) -> str:
        """构造 FTS5 索引正文：content + keywords 分词后空格连接（keywords 的 JSON 语法字符被分词自然过滤）"""
        parts = [content]
        if isinstance(keywords, str) and keywords.strip():
            parts.append(keywords)
        return " ".join(tokenize_for_fts(" ".join(parts)))

    def _rebuild_fts_index(self, conn):
        """全量重建 FTS5 关键词索引，仅索引 status=valid 切片，与向量路同步范围一致"""
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM {FTS_TABLE}")
        rows = cursor.execute(
            "SELECT knowledge_id, content, keywords FROM knowledge_chunk WHERE status = 'valid'"
        ).fetchall()
        cursor.executemany(
            f"INSERT INTO {FTS_TABLE} (knowledge_id, body) VALUES (?, ?)",
            [(kid, self._fts_body(content, kw)) for kid, content, kw in rows],
        )
        return len(rows)

    def _ensure_fts_index(self):
        """FTS5 行数与主库有效切片行数对账，不一致（如旧库升级/索引缺失）时自动补建"""
        if not self.fts_available:
            return
        conn = self._get_conn()
        try:
            n_fts = conn.execute(f"SELECT COUNT(*) FROM {FTS_TABLE}").fetchone()[0]
            n_valid = conn.execute(
                "SELECT COUNT(*) FROM knowledge_chunk WHERE status = 'valid'"
            ).fetchone()[0]
            if n_fts != n_valid:
                self._rebuild_fts_index(conn)
                conn.commit()
                print(f"FTS5 关键词索引已自动重建，共索引 {n_valid} 条有效切片")
        finally:
            conn.close()

    # ---------- 数据导入与查询 ----------

    def load_excel_to_db(self):
        """全量导入 Excel 数据，覆盖旧数据，保证数据与 Excel 权威源一致"""
        df_doc = self._clean_doc_version(pd.read_excel(EXCEL_PATH, sheet_name="doc_version"))
        df_chunk = self._clean_knowledge_chunk(pd.read_excel(EXCEL_PATH, sheet_name="knowledge_chunk"))
        self._validate(df_doc, df_chunk)

        # 剩余 NaN（如缺失的 expire_date）统一转 None，SQLite 存 NULL
        df_doc = df_doc.where(pd.notna(df_doc), None)
        df_chunk = df_chunk.where(pd.notna(df_chunk), None)

        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            # 全量覆盖：清空后按固定列序插入（不用 to_sql replace，避免丢掉 CHECK/外键/索引等约束）
            cursor.execute("DELETE FROM knowledge_chunk")
            cursor.execute("DELETE FROM doc_version")
            cursor.executemany(
                f"INSERT INTO doc_version ({','.join(DOC_COLUMNS)}) "
                f"VALUES ({','.join(['?'] * len(DOC_COLUMNS))})",
                df_doc.itertuples(index=False, name=None),
            )
            cursor.executemany(
                f"INSERT INTO knowledge_chunk ({','.join(CHUNK_COLUMNS)}) "
                f"VALUES ({','.join(['?'] * len(CHUNK_COLUMNS))})",
                df_chunk.itertuples(index=False, name=None),
            )
            # 关键词索引随主库数据在同一事务内全量重建，保证与主库强一致
            n_fts = self._rebuild_fts_index(conn)
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise ValueError(f"Excel 数据违反表约束（CHECK/UNIQUE/外键）：{e}") from e
        finally:
            conn.close()
        if self.fts_available:
            print(f"FTS5 关键词索引重建完成，共索引 {n_fts} 条有效切片")
        return len(df_doc), len(df_chunk)

    def verify_doc_files(self) -> list[dict]:
        """校验 doc_version.file_path 与 data/raw 下原始文档的对应关系，返回逐条校验报告"""
        # 以规范化文件名建索引，容忍空白/中点字符差异实现模糊匹配
        norm_index = {normalize_filename(p.name): p for p in RAW_DATA_DIR.rglob("*.txt")}

        conn = self._get_conn()
        df = pd.read_sql("SELECT doc_version_id, doc_name, file_path FROM doc_version", conn)
        conn.close()

        report = []
        for _, row in df.iterrows():
            fp = row["file_path"] or ""
            local = RAW_DATA_DIR / fp.lstrip("/") if fp else None
            if local and local.exists():
                result, matched = "ok", str(local)
            else:
                fuzzy_path = norm_index.get(normalize_filename(Path(fp).name))
                result, matched = ("fuzzy", str(fuzzy_path)) if fuzzy_path else ("missing", None)
            report.append({
                "doc_version_id": row["doc_version_id"],
                "doc_name": row["doc_name"],
                "file_path": fp,
                "result": result,
                "matched_path": matched,
            })
        return report

    def get_valid_knowledge_with_meta(self) -> pd.DataFrame:
        """获取文档与切片双有效的知识切片，携带文档元数据（生效/失效时间），供向量同步使用"""
        conn = self._get_conn()
        query = '''
        SELECT
            k.knowledge_id, k.doc_version_id, k.clause_position, k.heading_path,
            k.chunk_type, k.content, k.original_text, k.keywords, k.status,
            d.doc_id, d.doc_name, d.version, d.doc_type, d.publish_dept,
            d.publish_date, d.effective_date, d.expire_date
        FROM knowledge_chunk k
        JOIN doc_version d ON k.doc_version_id = d.doc_version_id
        WHERE k.status = 'valid' AND d.status = 'valid'
        '''
        df = pd.read_sql(query, conn)
        conn.close()
        return df

    def keyword_search(self, query: str, top_k: int = 10) -> list[dict]:
        """FTS5 关键词检索：BM25 精准召回，过滤切片/文档双有效且在生效期内。

        出参与向量路检索结果同构（不含 similarity），另附：
            keyword_score  BM25 原始分按当前查询最优值归一化到 [0,1]，保证不同查询间可比
            coverage       查询词元在切片正文（content+keywords）中的命中率，供弱命中诊断
        FTS5 不可用或分词后无有效词元时返回空列表，由上层退化为纯向量检索。
        """
        if not self.fts_available:
            return []
        tokens = tokenize_for_fts(query)
        if not tokens:
            return []
        # OR 连接扩大召回面，BM25 自然偏向命中词更多的切片；双引号包裹避免词元撞上 FTS5 查询语法
        match_expr = " OR ".join(f'"{t}"' for t in sorted(set(tokens)))
        query_tokens = set(tokens)

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f'''
                SELECT k.knowledge_id, k.doc_version_id, k.clause_position, k.heading_path,
                       k.chunk_type, k.content, k.original_text, k.keywords,
                       d.doc_name, d.doc_type, d.version, d.publish_dept, d.effective_date,
                       bm25({FTS_TABLE}) AS raw_score
                FROM {FTS_TABLE} f
                JOIN knowledge_chunk k ON f.knowledge_id = k.knowledge_id
                JOIN doc_version d ON k.doc_version_id = d.doc_version_id
                WHERE {FTS_TABLE} MATCH ?
                  AND k.status = 'valid' AND d.status = 'valid'
                  AND (d.effective_date IS NULL OR d.effective_date <= date('now'))
                  AND (d.expire_date IS NULL OR d.expire_date >= date('now'))
                ORDER BY raw_score
                LIMIT ?
                ''',
                (match_expr, top_k),
            ).fetchall()
        except sqlite3.OperationalError as e:
            print(f"[警告] FTS5 关键词检索失败，退化为纯向量检索：{e}")
            return []
        finally:
            conn.close()

        if not rows:
            return []
        # BM25 原始分为负值（越小越相关），取绝对值后按当前查询最大值归一化
        max_raw = max(abs(r[-1]) for r in rows) or 1.0
        results = []
        for (kid, doc_vid, clause, heading, ctype, content, original, kw_raw,
             doc_name, doc_type, version, dept, eff_date, raw_score) in rows:
            body_tokens = set(tokenize_for_fts(f"{content} {kw_raw or ''}"))
            coverage = len(query_tokens & body_tokens) / len(query_tokens)
            results.append({
                "knowledge_id": kid,
                "doc_version_id": doc_vid,
                "chunk_type": ctype,
                "content": content,
                "original_text": original,
                "clause_position": clause,
                "heading_path": heading,
                "keywords": parse_keywords(kw_raw),
                "doc_name": doc_name,
                "doc_type": doc_type,
                "version": version,
                "publish_dept": dept,
                "effective_date": eff_date,
                "keyword_score": round(abs(raw_score) / max_raw, 4),
                "coverage": round(coverage, 4),
            })
        return results

    def get_knowledge_detail_by_ids(self, knowledge_ids: list[str]) -> pd.DataFrame:
        """根据切片 ID 批量查询完整详情，用于检索后补全信息"""
        if not knowledge_ids:
            return pd.DataFrame()
        conn = self._get_conn()
        placeholders = ",".join(["?"] * len(knowledge_ids))
        query = f'''
        SELECT
            k.knowledge_id, k.doc_version_id, k.clause_position, k.heading_path,
            k.chunk_type, k.content, k.original_text, k.keywords,
            d.doc_id, d.doc_name, d.version, d.doc_type, d.publish_dept,
            d.publish_date, d.effective_date, d.expire_date
        FROM knowledge_chunk k
        JOIN doc_version d ON k.doc_version_id = d.doc_version_id
        WHERE k.knowledge_id IN ({placeholders})
        '''
        df = pd.read_sql(query, conn, params=knowledge_ids)
        conn.close()
        return df


# 全局实例
sqlite_client = SqliteClient()
