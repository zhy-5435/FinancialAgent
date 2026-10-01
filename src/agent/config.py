"""Agent 层配置：加载项目根目录 .env，集中管理模型接入与检索服务参数"""
import json
import os

from dotenv import load_dotenv

from src.config import PROJECT_ROOT

# .env 位于项目根目录（不入库，存放大模型 API Key 等敏感信息）
load_dotenv(PROJECT_ROOT / ".env")

# ---------- LLM 配置（OpenAI 兼容接口，腾讯云 MaaS） ----------
LLM_MODEL = os.getenv("LLM_MODEL", "GLM-5.1")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://tokenhub.tencentmaas.com/v1")
# .env 中变量名为 API_KEY（兼容 OPENAI_API_KEY 命名）
LLM_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))

# ---------- 关闭推理模型「思考模式」（降低首 token 延迟） ----------
# 仅对支持该参数的模型生效（实测 GLM-5.3-flash / flashx / glm-5-turbo 支持，关闭后单次调用
# 由 ~3-9s 降至 ~0.5s；GLM-5.1 强制思考，会忽略此参数）。不支持的端点忽略该参数，不报错。
LLM_DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "false").lower() in ("1", "true", "yes")


def _build_extra_body() -> dict | None:
    """组装透传给 OpenAI 兼容接口的 extra_body：thinking 开关 + 通用 JSON 扩展合并"""
    body: dict = {}
    if LLM_DISABLE_THINKING:
        body["thinking"] = {"type": "disabled"}
    raw = os.getenv("LLM_EXTRA_BODY")  # 预留：额外自定义参数（JSON 字符串）
    if raw:
        try:
            body.update(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return body or None


LLM_EXTRA_BODY = _build_extra_body()

# ---------- L2 检索服务 ----------
SEARCH_API_BASE = os.getenv("SEARCH_API_BASE", "http://127.0.0.1:8000")
SEARCH_TOP_K = int(os.getenv("SEARCH_TOP_K", "5"))

# ---------- 金融场景可靠性 ----------
# 相似度低于阈值的切片不作为作答依据；全部低于阈值时明确拒答，禁止编造条款与费率
# 0.4 为 bge-base-zh-v1.5 实测校准值（正例最低 0.4354，沿用 0.5 会误杀正确结果）
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.4"))

# ---------- 意图识别 ----------
# 分类置信度低于阈值的非 kb_qa 意图一律回落知识库分支（保留现有拒答底线，宁可拒答不乱分流）
INTENT_CONFIDENCE_THRESHOLD = float(os.getenv("INTENT_CONFIDENCE_THRESHOLD", "0.6"))

# 关键词/规则预分类：命中 news/quote/chitchat 直接短路分流、跳过意图 LLM；未命中仍走 LLM
INTENT_RULES_ENABLED = os.getenv("INTENT_RULES_ENABLED", "true").lower() in ("1", "true", "yes")

# ---------- L3 Agent HTTP 服务 ----------
AGENT_API_HOST = os.getenv("AGENT_API_HOST", "0.0.0.0")
AGENT_API_PORT = int(os.getenv("AGENT_API_PORT", "8001"))
# 允许跨域访问前端来源，逗号分隔；生产同域反代时可置空关闭 CORS
AGENT_CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("AGENT_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]
