"""Инструмент поиска в интернете через DuckDuckGo."""

import asyncio
import urllib.parse

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


@tool(
    name="web_search",
    description="Ищет информацию в интернете. "
    "Используйте для получения актуальных данных, документации, примеров кода.",
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
)
async def web_search(query: str, max_results: int = 5) -> str:
    """Ищет в интернете через DuckDuckGo (HTML API).

    Args:
        query: Поисковый запрос
        max_results: Количество результатов (1-10)

    Returns:
        Результаты поиска
    """
    try:
        max_results = int(max_results)
    except (ValueError, TypeError):
        max_results = 5
    n = min(max(max_results, 1), 10)

    for attempt in range(3):
        try:
            import httpx

            params = {
                "q": query,
                "ia": "web",
            }
            headers = {
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://duckduckgo.com/",
                "DNT": "1",
            }
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params=params,
                    headers=headers,
                )
                if resp.status_code == 202:
                    if attempt < 2:
                        wait = 2 ** (attempt + 1)
                        logger.warning(f"DDG rate limit, retrying in {wait}s...")
                        await asyncio.sleep(wait)
                        continue
                    return "Поиск временно недоступен — DuckDuckGo вернул 202 (rate limit)."

                resp.raise_for_status()

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for item in soup.select(".result"):
                title_el = item.select_one(".result__title a")
                snippet_el = item.select_one(".result__snippet")
                if title_el:
                    title = title_el.get_text(strip=True)
                    href = title_el.get("href", "")
                    # DDG wraps href in redirect
                    if href.startswith("//"):
                        href = "https:" + href
                    body = snippet_el.get_text(strip=True) if snippet_el else ""
                    results.append((title, href, body))

            if not results:
                return "Ничего не найдено."

            parts = []
            for i, (title, href, body) in enumerate(results[:n], 1):
                parts.append(f"{i}. {title}\n   {href}\n   {body[:300]}")

            logger.debug(f"Веб-поиск: {query} -> {len(results)} результатов")
            return "\n\n".join(parts)

        except ImportError:
            # Fallback to duckduckgo_search library
            try:
                from duckduckgo_search import DDGS

                ddgs_results = []
                with DDGS(headers={"User-Agent": _USER_AGENT}) as ddgs:
                    for r in ddgs.text(query, max_results=n):
                        ddgs_results.append(r)

                if not ddgs_results:
                    return "Ничего не найдено."

                parts = []
                for i, r in enumerate(ddgs_results, 1):
                    title = r.get("title", "Без заголовка")
                    href = r.get("href", "")
                    body = r.get("body", "")
                    parts.append(f"{i}. {title}\n   {href}\n   {body[:300]}")

                logger.debug(f"Веб-поиск: {query} -> {len(ddgs_results)} результатов")
                return "\n\n".join(parts)
            except ImportError:
                return "Для поиска требуется установить: pip install httpx beautifulsoup4 lxml"

        except Exception as e:
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                logger.warning(f"Ошибка веб-поиска: {e}, retry {attempt+1}/3 через {wait}s")
                await asyncio.sleep(wait)
                continue
            logger.error(f"Ошибка веб-поиска: {e}")
            return f"Ошибка поиска: {e}"
