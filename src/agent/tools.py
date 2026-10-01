"""工具层：将 L2 检索、权威信源联网检索与外部行情源封装为**异步** LangChain 工具，
供 Agent Loop 的 reason 节点 bind 给 LLM 自主选择、act 节点并发执行。

约定（薄封装惯例）：本模块只做「异步化 + 阈值/字段归一」的薄封装，核心业务逻辑仍下沉在
web_search / quote_service 服务模块；导入时把三工具注册进 registry.ToolRegistry，
图节点从注册表取工具、不 import 具体函数。
"""
import asyncio

import httpx
from langchain_core.tools import tool

from src.agent.config import (
    SEARCH_API_BASE,
    SEARCH_TOP_K,
    SIMILARITY_THRESHOLD,
    TOOL_TIMEOUT_KB,
    TOOL_TIMEOUT_QUOTE,
    TOOL_TIMEOUT_WEB,
)
from src.agent.registry import ToolSpec, registry
from src.agent.web_search import search_and_package


@tool
async def search_knowledge(query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """在 L1 金融知识库中检索权威知识切片（监管规则 / 内部制度 / 产品说明书）。

    回答任何金融业务问题（条款、费率、期限、风险等级、内部管理制度等）前必须先调用本工具。
    仅返回状态有效、在生效期内且**相似度达标**的切片列表（低于阈值的结果已被本工具过滤），
    每条含：content（语义内容）、original_text（原文摘录）、doc_name / doc_type / version /
    clause_position / heading_path（溯源信息）、similarity（余弦相似度）、keyword_score、
    score（综合得分，按综合得分降序）。返回空列表表示知识库中无足够权威依据可作答。
    """
    async with httpx.AsyncClient(timeout=TOOL_TIMEOUT_KB) as client:
        resp = await client.post(
            f"{SEARCH_API_BASE}/search",
            json={"query": query, "top_k": top_k},
        )
        resp.raise_for_status()
        results = resp.json()["results"]
    # 相似度阈值过滤下沉到工具层：低于阈值者不作为作答依据（守「无有效证据即拒答」底线）
    return [h for h in results if h.get("similarity", 0) >= SIMILARITY_THRESHOLD]


@tool
async def web_search(query: str) -> str:
    """在权威金融信源联网检索并抓取原文。

    信源为白名单内的权威站点：主信源是证监会指定信息披露的财经媒体，
    政策信源是监管机构、交易所与行业协会官方网站。
    当问题需要时效性信息（最新政策、监管动态、市场行情、数据发布）或知识库未覆盖时调用。
    仅检索并返回白名单站点内容，每条含站点名、标题、原文链接、发布时间与正文摘录，
    供作答引用 URL；无命中时返回"（联网未检索到权威信源结果）"。
    """
    # Tavily / 直连抓取为阻塞 IO，投递到线程避免阻塞事件循环
    return await asyncio.to_thread(search_and_package, query)


@tool
async def search_realtime_quote(symbol: str, query: str = "") -> dict:
    """查询股票/ETF/指数的实时行情快照（新浪财经公开行情接口，含硬超时与降级）。

    symbol 为标的代码（如 600519）或名称（如 沪深300、贵州茅台），可为空（
    此时从 query 问句中识别标的）。返回：
        ok        是否成功取数；False 时 markdown 为固定降级话术
        markdown  整理好的回答（表格 + 来源 + 免责，数字不经 LLM 转写）
        data      结构化快照（name/price/change_pct/quote_time/source 等），无数据时为 None
    """
    # 延迟导入：行情服务仅相关查询需要，不在其他场景预热网络调用依赖
    from src.agent.quote_service import get_market_quote

    # get_market_quote 内部已带硬超时与线程池，这里再投递一次避免阻塞事件循环
    return await asyncio.to_thread(get_market_quote, symbol, query)


# ---------- 注册到工具注册表（含超时 / 失败语义 / 意图域 / 信任级） ----------
registry.register(ToolSpec(
    name="search_knowledge", tool=search_knowledge,
    intent_hint="kb_qa", trust_level="internal-kb",
    timeout_s=TOOL_TIMEOUT_KB, failure_class="hard",
))
registry.register(ToolSpec(
    name="web_search", tool=web_search,
    intent_hint="news_search", trust_level="web",
    timeout_s=TOOL_TIMEOUT_WEB, failure_class="degradable",
))
registry.register(ToolSpec(
    name="search_realtime_quote", tool=search_realtime_quote,
    intent_hint="quote_query", trust_level="tool",
    timeout_s=TOOL_TIMEOUT_QUOTE, failure_class="degradable",
))
