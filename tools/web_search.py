"""Инструмент поиска в интернете через DuckDuckGo с несколькими стратегиями."""

import asyncio

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

async def _search_ddgs_api(query: str, max_results: int) -> list:
    """Использует duckduckgo_search с backend='api'."""
    from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region='ru-ru', backend='api'))

async def _search_ddgs_html(query: str, max_results: int) -> list:
    """Использует duckduckgo_search с backend='html'."""
    from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region='ru-ru', backend='html'))

async def _search_curl_cffi(query: str, max_results: int) -> list:
    """Использует curl_cffi для прямого запроса HTML."""
    from bs4 import BeautifulSoup
    from curl_cffi import requests
    url = "https://html.duckduckgo.com/html/"
    params = {"q": query, "ia": "web"}
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://duckduckgo.com/",
        "DNT": "1",
    }
    session = requests.Session()
    resp = session.get(url, params=params, headers=headers, impersonate="chrome", timeout=30)
    if resp.status_code != 200:
        raise Exception(f"HTTP {resp.status_code}")
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select(".result"):
        title_el = item.select_one(".result__title a")
        snippet_el = item.select_one(".result__snippet")
        if title_el:
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if href.startswith("//"):
                href = "https:" + href
            body = snippet_el.get_text(strip=True) if snippet_el else ""
            results.append({"title": title, "href": href, "body": body})
    return results

@tool(
    name="web_search",
    description="Ищет информацию в интернете. "
                "Используйте для получения актуальных данных, документации, примеров кода.",
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
)
async def web_search(query: str, max_results: int = 5, search_cache=None) -> str:
    """Ищет в интернете через DuckDuckGo (HTML API).
    Args:
        query: Поисковый запрос
        max_results: Количество результатов (1-10)
    Returns:
        Результаты поиска
    """
    if search_cache:
        cached = search_cache.get(query)
        if cached:
            return cached

    try:
        max_results = int(max_results)
    except (ValueError, TypeError):
        max_results = 5
    n = min(max(max_results, 1), 10)

    strategies = [
        ("DDGS API", _search_ddgs_api),
        ("DDGS HTML", _search_ddgs_html),
        ("curl_cffi", _search_curl_cffi),
    ]
    result = None

    for attempt in range(2):
        for name, strategy in strategies:
            try:
                results = await strategy(query, n)
                if results:
                    parts = []
                    for i, r in enumerate(results[:n], 1):
                        title = r.get("title", "Без заголовка")
                        href = r.get("href", "")
                        body = r.get("body", "")
                        parts.append(f"{i}. {title}\n   {href}\n   {body[:300]}")
                    result = "\n\n".join(parts)
                    logger.debug(f"Веб-поиск ({name}): {query} -> {len(results)} результатов")
                    if search_cache and result:
                        search_cache.set(query, result)
                    return result
            except Exception as e:
                logger.warning(f"Стратегия {name} не удалась: {e}")
                await asyncio.sleep(1)
        if attempt == 0:
            logger.warning("Все стратегии не дали результатов, повтор через 3 секунды...")
            await asyncio.sleep(3)

    return "Поиск временно недоступен. Попробуйте позже или измените запрос."
