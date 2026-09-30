"""工具层：将 L2 检索 REST 服务封装为 LangChain 工具，供 Agent / 图节点调用"""
import httpx
from langchain_core.tools import tool

from src.agent.config import SEARCH_API_BASE, SEARCH_TOP_K


@tool
def search_knowledge(query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """在 L1 金融知识库中检索权威知识切片（监管规则 / 内部制度 / 产品说明书）。

    回答任何金融业务问题前必须先调用本工具。返回切片列表，每条含：
    content（语义内容）、original_text（原文摘录）、doc_name / doc_type / version /
    clause_position / heading_path（溯源信息）、similarity（余弦相似度）。
    仅返回状态有效且在生效期内的切片。
    """
    resp = httpx.post(
        f"{SEARCH_API_BASE}/search",
        json={"query": query, "top_k": top_k},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["results"]
