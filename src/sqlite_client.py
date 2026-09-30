import sqlite3
from pathlib import Path

import pandas as pd

from src.config import SQLITE_DB_PATH, EXCEL_PATH, RAW_DATA_DIR

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
        self._init_tables()

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

    # ---------- 数据导入与查询 ----------

    def load_excel_to_db(self):
        """全量导入Excel数据，覆盖旧数据，个人测试场景下保证数据与Excel一致"""
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
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise ValueError(f"Excel 数据违反表约束（CHECK/UNIQUE/外键）：{e}") from e
        finally:
            conn.close()
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

    def get_valid_knowledge_with_meta(self):
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

    def get_knowledge_detail_by_ids(self, knowledge_ids: list[str]) -> pd.DataFrame:
        """根据切片ID批量查询完整详情，用于检索后补全信息"""
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
