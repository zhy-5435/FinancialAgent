"""工具层：将 L2 检索 REST 服务与外部行情源封装为 LangChain 工具，供 Agent / 图节点调用"""
import httpx
from langchain_core.tools import tool

from src.agent.config import SEARCH_API_BASE, SEARCH_TOP_K


@tool
def search_knowledge(query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """在 L1 金融知识库中检索权威知识切片（监管规则 / 内部制度 / 产品说明书）。

    回答任何金融业务问题前必须先调用本工具。返回切片列表，每条含：
    content（语义内容）、original_text（原文摘录）、doc_name / doc_type / version /
    clause_position / heading_path（溯源信息）、similarity（余弦相似度）、
    keyword_score（关键词得分）、score（综合得分，按综合得分降序）。
    仅返回状态有效且在生效期内的切片。
    """
    resp = httpx.post(
        f"{SEARCH_API_BASE}/search",
        json={"query": query, "top_k": top_k},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["results"]


@tool
def search_realtime_quote(symbol: str, query: str = "") -> dict:
    """查询股票/ETF/指数的实时行情快照（新浪财经公开行情接口，含硬超时与降级）。

    symbol 为标的代码（如 600519）或名称（如 沪深300、贵州茅台），可为空（
    此时从 query 问句中识别标的）。返回：
        ok        是否成功取数；False 时 markdown 为固定降级话术
        markdown  整理好的回答（表格 + 来源 + 免责，数字不经 LLM 转写）
        data      结构化快照（name/price/change_pct/quote_time/source 等），无数据时为 None
    """
    # 延迟导入：行情服务仅 quote_query 分支需要，不在其他分支预热网络调用依赖
    from src.agent.quote_service import get_market_quote

    return get_market_quote(symbol, query)
