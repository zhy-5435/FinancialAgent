"""会话记忆层配置：加载项目根 .env，集中管理持久化路径、上下文预算与压缩档位阈值

设计原则（与规划一致）：
    - 记忆库 l1_memory.db 与权威知识库 l1_core.db 物理隔离，互不污染；
    - Token 计量走字符启发式（无新增依赖），预算上限 CONTEXT_MAX_TOKENS 按实际模型窗口 .env 覆盖；
    - 4 档梯度压缩阈值、保留轮数、脱敏开关、摘要模型均集中于此，改配置不改代码。
"""
import os

from dotenv import load_dotenv

from src.config import MEMORY_DB_PATH, PROJECT_ROOT

# .env 位于项目根目录（与 agent 层共用同一份，不入库）
load_dotenv(PROJECT_ROOT / ".env")

# ---------- 持久化存储 ----------
# 会话消息与压缩元数据落库路径（独立 SQLite）
MEMORY_DB_PATH = MEMORY_DB_PATH

# 存档入库前是否做字段级 PII 脱敏（身份证/手机/银行卡/邮箱）
MASKING_ENABLED = os.getenv("MEMORY_MASKING_ENABLED", "true").lower() in ("1", "true", "yes")

# ---------- 上下文预算与 Token 计量 ----------
# 模型上下文窗口预算（字符启发式估算的 token 上限）：默认保守 32000，按实际模型 .env 覆盖
CONTEXT_MAX_TOKENS = int(os.getenv("CONTEXT_MAX_TOKENS", "32000"))
# 为模型输出预留的 token：可用预算 = CONTEXT_MAX_TOKENS - CONTEXT_OUTPUT_RESERVE
CONTEXT_OUTPUT_RESERVE = int(os.getenv("CONTEXT_OUTPUT_RESERVE", "2000"))

# ---------- 4 档梯度压缩阈值（占可用预算的比例）----------
# < TIER_WARNING 直装；[WARNING, GLOBAL) 预警局部摘要；[GLOBAL, TRIM) 全局摘要；>= TRIM 直接裁剪
TIER_WARNING = float(os.getenv("CONTEXT_TIER_WARNING", "0.70"))
TIER_GLOBAL = float(os.getenv("CONTEXT_TIER_GLOBAL", "0.82"))
TIER_TRIM = float(os.getenv("CONTEXT_TIER_TRIM", "0.92"))

# ---------- 保留轮数 ----------
# 预警档：最近 N 轮完整保留（不参与局部摘要）
RECENT_FULL_ROUNDS = int(os.getenv("CONTEXT_RECENT_FULL_ROUNDS", "3"))
# 全局摘要档：最近 1~2 轮完整保留
RECENT_KEEP_ROUNDS = int(os.getenv("CONTEXT_RECENT_KEEP_ROUNDS", "2"))

# ---------- 锁存实体 ----------
# 锁存实体（标的代码/产品名等）是否强制保留（任何档位不裁剪、不入摘要）
LATCHED_ENTITIES_ENABLED = os.getenv("CONTEXT_LATCHED_ENTITIES", "true").lower() in ("1", "true", "yes")

# ---------- 摘要 ----------
# 摘要复用 L3 的 LLM 模型（缺省与 LLM_MODEL 一致，可独立覆盖以选更省的模型做摘要）
SUMMARIZER_MODEL = os.getenv("MEMORY_SUMMARIZER_MODEL", os.getenv("LLM_MODEL", "GLM-5.1"))
# 单次摘要输出上限（token 估算口径），防止摘要过长反噬预算
MAX_SUMMARY_TOKENS = int(os.getenv("MEMORY_MAX_SUMMARY_TOKENS", "600"))

# ---------- 工具结果精简 ----------
# 历史工具结果（检索切片/网搜素材）在上下文中保留的最大条数，超出仅保留高分条目
TOOL_RESULT_KEEP_HITS = int(os.getenv("MEMORY_TOOL_RESULT_KEEP_HITS", "3"))
