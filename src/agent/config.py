"""Agent 层配置：加载项目根目录 .env，集中管理模型接入与检索服务参数"""
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

# ---------- L2 检索服务 ----------
SEARCH_API_BASE = os.getenv("SEARCH_API_BASE", "http://127.0.0.1:8000")
SEARCH_TOP_K = int(os.getenv("SEARCH_TOP_K", "5"))

# ---------- 金融场景可靠性 ----------
# 相似度低于阈值的切片不作为作答依据；全部低于阈值时明确拒答，禁止编造条款与费率
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.5"))
