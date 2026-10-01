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

# ---------- 实时行情工具（M4） ----------
# 行情接口硬超时（秒）：单标的直连正常 <1s，超时即降级，不阻断其他分支
QUOTE_TIMEOUT_SECONDS = float(os.getenv("QUOTE_TIMEOUT_SECONDS", "10"))
# 错名/错别字 LLM 推断命中阈值：低于该值视为猜不准，走未识别话术而非强行作答
QUOTE_FUZZY_MIN_CONFIDENCE = float(os.getenv("QUOTE_FUZZY_MIN_CONFIDENCE", "0.6"))
# 行情数据源与免责固定话术（按要求必须注明来源且不得加 LLM 生成）
QUOTE_SOURCE_NAME = "新浪财经公开行情接口（hq.sinajs.cn 实时快照）"
QUOTE_DISCLAIMER = "行情数据来自第三方公开接口，仅供参考、不构成任何投资建议，可能有延迟，请以交易所公布数据为准；本条回答非 L1 知识库权威内容。"

# 关键词/规则预分类：命中 news/quote/chitchat 直接短路分流、跳过意图 LLM；未命中仍走 LLM
INTENT_RULES_ENABLED = os.getenv("INTENT_RULES_ENABLED", "true").lower() in ("1", "true", "yes")

# ---------- Agent Loop（统一自主循环） ----------
# 循环最大步数（reason→act→observe 一轮记 1 步）；=1 近似旧单轮，作应急降级
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "5"))
# 整轮墙钟上限（秒）：超限则 finalize 输出部分结论 + 未完成声明，杜绝无限循环
AGENT_LOOP_TIMEOUT_SECONDS = float(os.getenv("AGENT_LOOP_TIMEOUT_SECONDS", "60"))
# 整轮 token 预算（0=不限，预留）
AGENT_TOKEN_BUDGET = int(os.getenv("AGENT_TOKEN_BUDGET", "0"))
# Agent Loop 总开关：true=走统一自主循环；false=应急回退到「一步内按意图预过滤工具」的近单轮形态
AGENT_LOOP_ENABLED = os.getenv("AGENT_LOOP_ENABLED", "true").lower() in ("1", "true", "yes")

# ---------- 工具层容错（超时 / 重试 / 熔断） ----------
# 分工具硬超时（秒）：知识库检索复用 L2 网络调用、网搜复用 WEB_SEARCH_TIMEOUT、行情复用 QUOTE_TIMEOUT_SECONDS
TOOL_TIMEOUT_KB = float(os.getenv("TOOL_TIMEOUT_KB", "30"))
TOOL_TIMEOUT_WEB = float(os.getenv("TOOL_TIMEOUT_WEB", str(int(os.getenv("WEB_SEARCH_TIMEOUT", "10")) * 2)))
TOOL_TIMEOUT_QUOTE = float(os.getenv("TOOL_TIMEOUT_QUOTE", str(os.getenv("QUOTE_TIMEOUT_SECONDS", "10"))))
# 单次工具调用失败后的最大重试次数（不含首次尝试）
TOOL_MAX_RETRIES = int(os.getenv("TOOL_MAX_RETRIES", "1"))
# 指数退避基数（秒），第 n 次重试等待 base * 2**n + 抖动
TOOL_RETRY_BASE_SECONDS = float(os.getenv("TOOL_RETRY_BASE_SECONDS", "0.5"))
# 熔断：连续失败达阈值打开，打开后 reset 秒内直接返回「工具不可用」观测，到期转半开探活
BREAKER_FAILURE_THRESHOLD = int(os.getenv("BREAKER_FAILURE_THRESHOLD", "3"))
BREAKER_RESET_SECONDS = float(os.getenv("BREAKER_RESET_SECONDS", "20"))

# ---------- 联网搜索（权威信源）----------
# 调用方（后续接入的 Agent / 检索流程）通过 web_search 工具触发联网检索：
# Tavily Search API 以 include_domains 限定在下列白名单官方域名内检索，
# 原文打包进 Prompt 并引用 URL；白名单同时用于结果二次过滤，杜绝不可控来源。
WEB_SEARCH_TIMEOUT = int(os.getenv("WEB_SEARCH_TIMEOUT", "10"))          # 单次 HTTP 超时（秒）
WEB_SEARCH_MAX_PAGES = int(os.getenv("WEB_SEARCH_MAX_PAGES", "5"))       # 打包进 Prompt 的网页数上限
WEB_SEARCH_MAX_CHARS = int(os.getenv("WEB_SEARCH_MAX_CHARS", "1500"))    # 每篇正文截断字数
# Tavily API Key（https://tavily.com 注册获取；未配置时联网搜索优雅降级为空结果）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
# Tavily 检索深度：basic 快；advanced 正文提取更全（消耗 2 倍额度）
TAVILY_SEARCH_DEPTH = os.getenv("TAVILY_SEARCH_DEPTH", "advanced")
# 财经资讯媒体（新闻、解读、快讯，证监会指定信息披露媒体）
WEB_MAIN_SOURCES = {
    "中证网": "cs.com.cn",
    "证券时报网": "stcn.com",
    "财联社": "cls.cn",
    "新华财经": "cnfin.com",
    "上海证券报·中国证券网": "cnstock.com",
    "证券日报网": "zqrb.cn",
    "金融时报·中国金融新闻网": "financialnews.com.cn",
    "经济参考报": "jjckb.cn",
}

# 政策信源（监管、政府、交易所官方，原文、公告、统计数据）
WEB_POLICY_SOURCES = {
    "中国人民银行": "pbc.gov.cn",
    "中国证监会": "csrc.gov.cn",
    "国家统计局": "stats.gov.cn",
    "国家金融监督管理总局": "nfra.gov.cn",
    "国家外汇管理局": "safe.gov.cn",
    "中华人民共和国财政部": "mof.gov.cn",
    "上海证券交易所": "sse.com.cn",
    "深圳证券交易所": "szse.cn",
    "北京证券交易所": "bse.cn",
    "中国金融期货交易所": "cffex.com.cn",
    "上海期货交易所": "shfe.com.cn",
    "大连商品交易所": "dce.com.cn",
    "郑州商品交易所": "czce.com.cn",
    "广州期货交易所": "gfex.com.cn",
    "中国证券投资基金业协会": "amac.org.cn",
    "中国证券业协会": "sac.net.cn",
    "中国证券登记结算": "chinaclear.cn",
    "中证指数有限公司": "csindex.com.cn",
}


# ---------- L3 Agent HTTP 服务 ----------
AGENT_API_HOST = os.getenv("AGENT_API_HOST", "0.0.0.0")
AGENT_API_PORT = int(os.getenv("AGENT_API_PORT", "8001"))
# 允许跨域访问前端来源，逗号分隔；生产同域反代时可置空关闭 CORS
AGENT_CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("AGENT_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]
