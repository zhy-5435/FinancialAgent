"""权威信源联网检索：基于 Tavily Search API 在白名单官方站点内搜索并抓取原文，打包为可直接注入 Prompt 的证据块。

流程：
    大模型给出的检索问题
      → Tavily 搜索（include_domains 限定 config 白名单内的主信源 / 政策信源域名，按相关度返回）
      → 域名白名单二次过滤（杜绝不可控来源）
      → 正文优先取 Tavily raw_content；过短或为空时回退直连抓取页面（BeautifulSoup 去标签、按字数截断）
      → 打包为带 [编号] | 站点 | 标题 | URL | 发布时间 | 原文摘录 的证据块，交 LLM 作答
无命中或全部抓取失败时返回明确的空标记，供上层判定，禁止编造。

注：改用 Tavily——原生支持 include_domains 域名限定且免反爬对抗；
而各站站内搜索经实测多为 JS 渲染 / 需签名接口（仅央行 wzdig 可静态 GET），通用引擎 site: 限定不可控。
"""
from __future__ import annotations

import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from src.agent.config import (
    TAVILY_API_KEY,
    TAVILY_SEARCH_DEPTH,
    WEB_MAIN_SOURCES,
    WEB_POLICY_SOURCES,
    WEB_SEARCH_MAX_CHARS,
    WEB_SEARCH_MAX_PAGES,
    WEB_SEARCH_TIMEOUT,
)

logger = logging.getLogger(__name__)

# 浏览器 UA：部分官网对默认 UA 返回反爬页面
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 全局复用的 HTTP 会话（调用 Tavily API 与直连补抓正文共用）
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(headers=_HEADERS, timeout=WEB_SEARCH_TIMEOUT, follow_redirects=True)
    return _client


def _http_get(url: str, retries: int = 1) -> httpx.Response:
    """带退避重试的 GET（应对偶发限流/网络抖动）。"""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _get_client().get(url)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(0.5 + random.random())
    raise last_exc  # 调用方自行捕获并降级

# 白名单：域名 -> 站点中文名（主信源 + 政策信源合并）
DOMAIN_TO_SITE: dict[str, str] = {
    **{d: name for name, d in WEB_MAIN_SOURCES.items()},
    **{d: name for name, d in WEB_POLICY_SOURCES.items()},
}

# 信源中文清单描述：由 config 白名单动态生成，供 Prompt 引用，避免枚举与实际域名漂移
SOURCE_DESC = (
    "主信源（财经媒体）：" + "、".join(WEB_MAIN_SOURCES)
    + "；政策信源（监管机构、交易所与行业协会官方）：" + "、".join(WEB_POLICY_SOURCES)
)
# 空结果标记：无命中或全部抓取失败时返回，供上层判空短路（不进 LLM 编造）
EMPTY_RESULT = "（联网未检索到权威信源结果）"


def _site_of(url: str) -> str | None:
    """按白名单域名命中返回站点中文名，非白名单返回 None。"""
    host = (urlparse(url).hostname or "").lower()
    for domain, name in DOMAIN_TO_SITE.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


_TAVILY_ENDPOINT = "https://api.tavily.com/search"

# raw_content 为页面 markdown/文本，其中 [文字](链接) 的链接部分对作答无益，展平只留文字
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _clean_text(text: str) -> str:
    """展平 markdown 链接、压缩连续空行，并按字数截断。"""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text[:WEB_SEARCH_MAX_CHARS]


def _tavily_search(query: str, max_results: int = 10) -> list[dict]:
    """调用 Tavily Search API 在白名单域名内检索，返回 [{title,url,snippet,text,site}]。

    include_domains 限定 config 白名单全部域名（含子域）；include_raw_content 尽量取整页正文，
    取不到时仅有 content 摘要；结果按相关度排序，并做白名单二次校验。
    """
    if not TAVILY_API_KEY:
        logger.error("未配置 TAVILY_API_KEY，联网搜索不可用（请在 .env 填入 Tavily API Key）")
        return []
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": TAVILY_SEARCH_DEPTH,
        "include_domains": list(DOMAIN_TO_SITE.keys()),
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": True,
    }
    data: dict = {}
    for attempt in range(2):  # 偶发网络抖动退避重试一次
        try:
            resp = _get_client().post(_TAVILY_ENDPOINT, json=payload)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as e:
            logger.warning("Tavily 请求失败（第 %d 次）: %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(0.5 + random.random())
    hits: list[dict] = []
    for r in data.get("results", []):
        url = (r.get("url") or "").strip()
        site = _site_of(url)
        if not url or not site:  # 二次校验：仅保留确属白名单域名的结果
            continue
        hits.append({
            "title": (r.get("title") or "").strip(),
            "url": url,
            "snippet": (r.get("content") or "").strip(),
            "text": (r.get("raw_content") or "").strip(),
            "site": site,
        })
    return hits


def _extract_publish_date(soup: BeautifulSoup) -> str:
    """尽力抽取发布时间（meta / time 标签），取不到返回空串。"""
    for sel, attr in (
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="pubdate"]', "content"),
        ('meta[name="publishdate"]', "content"),
    ):
        tag = soup.select_one(sel)
        if tag and tag.get(attr):
            return tag[attr].strip()
    time_tag = soup.select_one("time")
    if time_tag:
        return (time_tag.get("datetime") or time_tag.get_text(strip=True)).strip()
    return ""


# 噪声容器的 class/id 关键词（导航/菜单/推荐/版权等），命中则整块剔除
_NOISE_RE = re.compile(
    r"nav|menu|crumb|sidebar|recommend|relative|banner|copyright|share|login|search|foot",
    re.I,
)


def _extract_text(soup: BeautifulSoup) -> str:
    """去脚本/导航等噪声，优先以 <p> 段落组装正文（避开多为 <a>/<li> 的菜单），按字数截断。"""
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav",
                     "aside", "form", "svg", "iframe", "button"]):
        if not tag.decomposed:  # decompose 会将子树元素标记为已析构，重复操作会报错
            tag.decompose()
    # 剔除 class/id 命中的噪声容器；select 返回预收集列表，已随祖先移除的元素需跳过
    for el in soup.select("[class],[id]"):
        if el.decomposed:
            continue
        keys = " ".join((el.get("class") or []) + [el.get("id") or ""])
        if keys and _NOISE_RE.search(keys):
            el.decompose()
    # 正文多在 <p>；段落法天然过滤导航/侧栏文本
    paras = [p.get_text(strip=True) for p in soup.select("p")]
    text = "\n".join(t for t in paras if len(t) >= 2)
    if len(text) < 150:  # 段落取不到足够正文（表格/纯 div 页面），退回全量去标签文本
        main = soup.body or soup
        text = "\n".join(line.strip() for line in main.stripped_strings if line.strip())
    text = re.sub(r"\n{2,}", "\n", text)
    return text[:WEB_SEARCH_MAX_CHARS]


def _fetch_page(url: str) -> tuple[str, str]:
    """抓取单页，返回 (正文文本, 发布时间)；失败返回空串。"""
    try:
        resp = _http_get(url, retries=1)
        # 非 HTML/纯文本（PDF/图片/下载流等）不抽正文，返回空串以回退到搜索摘要，避免乱码
        ctype = resp.headers.get("content-type", "").lower()
        if "html" not in ctype and "text" not in ctype and ctype:
            return "", ""
        soup = BeautifulSoup(resp.content, "html.parser")
        date = _extract_publish_date(soup)  # 先取时间（抽正文会改 soup 结构）
        return _extract_text(soup), date
    except Exception as e:
        logger.warning("抓取失败 %s: %s", url, e)
        return "", ""


def search_and_collect(query: str, max_pages: int = WEB_SEARCH_MAX_PAGES) -> list[dict]:
    """Tavily 白名单检索 + 过短正文直连补抓，返回结构化结果列表。"""
    hits = _tavily_search(query, max_results=max_pages * 2)
    if not hits:
        return []

    # raw_content 过短（仅摘要/被截断）的条目并发直连补抓（可同时拿到发布时间）
    need_fetch = [h for h in hits if len(h["text"]) < 150]
    with ThreadPoolExecutor(max_workers=4) as ex:
        fetch_map = {h["url"]: ex.submit(_fetch_page, h["url"]) for h in need_fetch}

    results: list[dict] = []
    for h in hits:
        if len(results) >= max_pages:
            break
        text, date = _clean_text(h["text"]), ""
        if len(text) < 150 and h["url"] in fetch_map:  # 回退直连抓取正文与发布时间
            text, date = fetch_map[h["url"]].result()
        if not text:  # 补抓也失败，退回搜索摘要，避免证据块出现空条目
            text = h.get("snippet", "")
        if text:
            results.append({
                "site": h["site"],
                "title": h["title"],
                "url": h["url"],
                "date": date,
                "text": text,
            })
    return results


def format_web_block(results: list[dict]) -> str:
    """将检索结果打包为带编号、站点、标题、URL、时间与原文的证据块文本。"""
    if not results:
        return EMPTY_RESULT
    blocks = []
    for i, r in enumerate(results, start=1):
        date = r.get("date") or "时间未知"
        blocks.append(
            f"[{i}] 来源：{r['site']} | 发布时间：{date}\n"
            f"标题：{r['title']}\n"
            f"链接：{r['url']}\n"
            f"原文：{r['text']}"
        )
    return "\n\n".join(blocks)


def search_and_package(query: str, max_pages: int = WEB_SEARCH_MAX_PAGES) -> str:
    """工具主入口：检索并打包为可直接注入 Prompt 的原文证据块字符串。"""
    return format_web_block(search_and_collect(query, max_pages))


WEB_PROMPT_TEMPLATE = """以下是从权威金融信源（{sources}）联网检索到的原文片段：

{web_block}

请仅依据上述原文片段回答用户问题：{query}
要求：
1. 只使用片段中的信息，禁止编造任何数字、条款、日期；片段未涉及的内容不回答。
2. 引用结论时标注对应编号（如 [1]），并给出站点、标题、链接与发布时间。
3. 若片段为空或与问题无关，直接回复“联网未检索到相关权威信息，暂无法作答”。
"""


def build_web_prompt(query: str, max_pages: int = WEB_SEARCH_MAX_PAGES) -> str:
    """打包这一步的成品入口：检索结果原文块嵌入 Prompt 模板，返回可直接交给 LLM 的完整 Prompt。"""
    web_block = search_and_package(query, max_pages)
    return WEB_PROMPT_TEMPLATE.format(web_block=web_block, query=query, sources=SOURCE_DESC)
