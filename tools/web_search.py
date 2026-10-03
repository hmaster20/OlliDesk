"""Инструмент поиска в интернете.

Стратегии (в порядке приоритета):
  1. DuckDuckGo Lite  — curl_cffi, минимальный HTML, стабильнее основного домена.
  2. DuckDuckGo HTML  — curl_cffi, основной HTML-эндпоинт.
  3. Wikipedia API    — для фактических запросов и определений.
  4. SearXNG          — публичные инстансы (best-effort, может быть отключён).

Порядок подобран эмпирически: в регионах, где DDG блокируется, Lite-версия
часто работает, а Wikipedia/ SearX дают хоть какой-то результат вместо пустоты.
"""

import asyncio
import urllib.parse

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from core.search_cache import SearchCache

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "DNT": "1",
}

# Публичные SearXNG-инстансы. Могут быть временно недоступны — обходим по кругу.
_SEARX_INSTANCES = (
    "https://searx.be",
    "https://search.bus-hit.me",
    "https://priv.au",
)


# ---------------------------------------------------------------------------
# Стратегия 1: DuckDuckGo Lite
# ---------------------------------------------------------------------------

async def _search_ddg_lite(query: str, max_results: int) -> list[dict[str, str]]:
    """DuckDuckGo Lite через curl_cffi (POST q=...)."""
    from bs4 import BeautifulSoup
    from curl_cffi import requests

    def _do() -> str:
        session = requests.Session()
        resp = session.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query},
            headers=_HEADERS,
            impersonate="chrome",
            timeout=20,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return resp.text

    html = await asyncio.to_thread(_do)
    soup = BeautifulSoup(html, "html.parser")

    results: list[dict[str, str]] = []
    # Lite-разметка: ссылки в <a class="result-link">, сниппет — следующий <td>.
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if not isinstance(href, str):
            continue
        if not href.startswith("http"):
            continue
        if "duckduckgo.com" in href:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        snippet = ""
        parent_tr = a.find_parent("tr")
        if parent_tr:
            tds = parent_tr.find_all("td")
            if len(tds) >= 2:
                snippet = tds[-1].get_text(strip=True)

        results.append({"title": title, "href": href, "body": snippet})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Стратегия 2: DuckDuckGo HTML
# ---------------------------------------------------------------------------

async def _search_ddg_html(query: str, max_results: int) -> list[dict[str, str]]:
    """DuckDuckGo HTML через curl_cffi (POST q=...)."""
    from bs4 import BeautifulSoup
    from curl_cffi import requests

    def _do() -> str:
        session = requests.Session()
        resp = session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "ia": "web"},
            headers=_HEADERS,
            impersonate="chrome",
            timeout=20,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return resp.text

    html = await asyncio.to_thread(_do)
    soup = BeautifulSoup(html, "html.parser")

    results: list[dict[str, str]] = []
    for item in soup.select(".result"):
        title_el = item.select_one(".result__title a, .result__a")
        snippet_el = item.select_one(".result__snippet")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        raw_href = title_el.get("href", "")
        href = raw_href if isinstance(raw_href, str) else ""
        if href.startswith("//"):
            href = "https:" + href
        # DDG часто отдаёт редирект вида /l/?uddg=<encoded>. Достаём целевой URL.
        if href.startswith("/l/") or "duckduckgo.com/l/" in href:
            try:
                parsed = urllib.parse.urlparse(href)
                qs = urllib.parse.parse_qs(parsed.query)
                if "uddg" in qs:
                    href = urllib.parse.unquote(qs["uddg"][0])
            except Exception:
                pass
        body = snippet_el.get_text(strip=True) if snippet_el else ""
        if title and href:
            results.append({"title": title, "href": href, "body": body})
        if len(results) >= max_results:
            break
    return results


# ---------------------------------------------------------------------------
# Стратегия 3: Wikipedia API
# ---------------------------------------------------------------------------

async def _search_wikipedia(query: str, max_results: int) -> list[dict[str, str]]:
    """Wikipedia API — только для фактических запросов."""
    import re

    from curl_cffi import requests

    def _do() -> dict:
        session = requests.Session()
        resp = session.get(
            "https://ru.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": max_results,
                "utf8": "1",
            },
            headers=_HEADERS,
            impersonate="chrome",
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return resp.json()

    data = await asyncio.to_thread(_do)
    hits = (data.get("query", {}) or {}).get("search", []) or []

    results: list[dict[str, str]] = []
    for hit in hits[:max_results]:
        title = hit.get("title", "")
        snippet = hit.get("snippet", "")
        snippet = re.sub(r"<[^>]+>", "", snippet)
        results.append(
            {
                "title": title,
                "href": f"https://ru.wikipedia.org/wiki/{urllib.parse.quote(title)}",
                "body": snippet,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Стратегия 4: SearXNG
# ---------------------------------------------------------------------------

async def _search_searx(query: str, max_results: int) -> list[dict[str, str]]:
    """SearXNG публичные инстансы (best-effort)."""
    from curl_cffi import requests

    last_err: Exception | None = None

    def _fetch(base: str) -> dict:
        session = requests.Session()
        resp = session.get(
            f"{base}/search",
            params={"q": query, "format": "json"},
            headers=_HEADERS,
            impersonate="chrome",
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"{base} HTTP {resp.status_code}")
        return resp.json()

    for base in _SEARX_INSTANCES:
        try:
            data = await asyncio.to_thread(_fetch, base)
            raw = data.get("results") or []
            results = []
            for r in raw[:max_results]:
                results.append(
                    {
                        "title": r.get("title", ""),
                        "href": r.get("url", ""),
                        "body": r.get("content", ""),
                    }
                )
            if results:
                return results
        except Exception as e:
            last_err = e
            logger.debug(f"SearX {base} failed: {type(e).__name__}: {e}")

    if last_err:
        raise last_err
    return []


# ---------------------------------------------------------------------------
# Публичный инструмент
# ---------------------------------------------------------------------------

@tool(
    name="web_search",
    description=(
        "Ищет АКТУАЛЬНУЮ информацию в интернете. ОБЯЗАТЕЛЬНО использовать для "
        "вопросов про свежие события, новости, курсы, погоду, сегодняшнюю дату, "
        "любые данные, которые могли измениться. НЕ пытайся отвечать на такие "
        "вопросы из памяти — твои знания устарели."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
async def web_search(
    query: str,
    max_results: int = 5,
    search_cache: SearchCache | None = None,
) -> str:
    """Ищет в интернете.

    Args:
        query: Поисковый запрос
        max_results: Количество результатов (1-10)
        search_cache: Кэш (пробрасывается автоматически)

    Returns:
        Отформатированные результаты или сообщение об ошибке.
    """
    if not query or not query.strip():
        return "Ошибка: пустой поисковый запрос."

    if search_cache:
        cached = search_cache.get(query)
        if cached:
            return cached

    try:
        n = max(1, min(int(max_results), 10))
    except (ValueError, TypeError):
        n = 5

    strategies = [
        ("DDG Lite", _search_ddg_lite),
        ("DDG HTML", _search_ddg_html),
        ("Wikipedia", _search_wikipedia),
        ("SearX", _search_searx),
    ]

    errors: list[str] = []
    for name, strategy in strategies:
        try:
            results = await strategy(query, n)
            if results:
                parts = []
                for i, r in enumerate(results, 1):
                    title = r.get("title", "Без заголовка")
                    href = r.get("href", "")
                    body = (r.get("body", "") or "")[:400]
                    parts.append(f"{i}. {title}\n   {href}\n   {body}")
                text = "\n\n".join(parts)
                logger.info(
                    f"web_search: '{query}' via {name}, {len(results)} results"
                )
                if search_cache:
                    search_cache.set(query, text)
                return text
            errors.append(f"{name}: no results")
        except Exception as e:
            logger.warning(
                f"web_search {name} failed: {type(e).__name__}: {e}"
            )
            errors.append(f"{name}: {type(e).__name__}")

    logger.error(f"web_search: all strategies failed for '{query}': {errors}")
    return (
        f"Не удалось найти информацию по запросу «{query}». "
        f"Все стратегии поиска вернули ошибку. Попробуйте переформулировать запрос."
    )