"""LLM 实例构造：统一入口，供 graph 层使用"""
from langchain.chat_models import init_chat_model

from src.agent.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE

# 通过 init_chat_model 接入 OpenAI 兼容服务（腾讯云 MaaS / GLM）
llm = init_chat_model(
    model=LLM_MODEL,
    model_provider="openai",
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
    temperature=LLM_TEMPERATURE,
)
