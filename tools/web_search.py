"""Инструмент поиска в интернете.

Стратегии (в порядке приоритета):
  1. Яндекс (парсинг HTML через curl_cffi) — основной для РФ.
  2. DDGS (ddgs / duckduckgo_search)       — старый проверенный движок.
  3. DDG HTML (curl_cffi)                  — прямой HTML-парсинг.
  4. SearXNG (публичные инстансы)          — последний резерв.

Порядок подобран эмпирически: Яндекс даёт лучшую выдачу для русскоязычных
запросов, DDGS — стабильный fallback. Если Яндекс ловит SmartCaptcha —
молча падаем на следующий движок.
"""

import asyncio
import urllib.parse
from typing import Any

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from core.search_cache import SearchCache
from core.security import NetworkMode, SecurityPolicy
from tools.web_search_hygiene import filter_query

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

_SEARX_INSTANCES = (
    "https://searx.be",
    "https://search.bus-hit.me",
    "https://priv.au",
)


# ---------------------------------------------------------------------------
# Стратегия 1: Яндекс (парсинг)
# ---------------------------------------------------------------------------


async def _search_yandex(query: str, max_results: int) -> list[dict[str, str]]:
    """Парсит выдачу yandex.ru/search/?text=... через curl_cffi."""
    from bs4 import BeautifulSoup
    from curl_cffi import requests

    def _do() -> str:
        session: requests.Session[Any] = requests.Session()
        resp = session.get(
            "https://yandex.ru/search/",
            params={"text": query},
            headers=_HEADERS,
            impersonate="chrome",
            timeout=25,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return str(resp.text)

    html = await asyncio.to_thread(_do)

    # Яндекс может вернуть капчу с HTTP 200 — проверяем содержимое.
    if "SmartCaptcha" in html or "captcha" in html.lower():
        raise RuntimeError("Yandex SmartCaptcha triggered")

    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, str]] = []

    # Селекторы Яндекса меняются, поэтому пробуем несколько вариантов.
    for item in soup.select("li.serp-item, li.serp-item_card"):
        title_el = item.select_one("h2 a, .organic__url-text, .OrganicTitle-Link")
        snippet_el = item.select_one(".organic__content-wrapper, .OrganicTextContentSpan")
        url_el = item.select_one("a.organic__url, a[href^='http']")

        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        href = ""
        if url_el:
            raw = url_el.get("href", "")
            href = raw if isinstance(raw, str) else ""
        if not href:
            raw_title_href = title_el.get("href", "")
            href = raw_title_href if isinstance(raw_title_href, str) else ""

        snippet = snippet_el.get_text(strip=True) if snippet_el else ""

        if title and href:
            results.append({"title": title, "href": href, "body": snippet})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Стратегия 2: DDGS (ddgs / duckduckgo_search)
# ---------------------------------------------------------------------------


async def _search_ddgs(query: str, max_results: int) -> list[dict[str, str]]:
    """DuckDuckGo через ddgs."""

    def _do() -> list[dict]:
        from ddgs import DDGS

        with DDGS() as ddgs:
            return list(
                ddgs.text(
                    query,
                    max_results=max_results,
                    region="ru-ru",
                )
            )

    raw = await asyncio.to_thread(_do)
    return [
        {
            "title": r.get("title", ""),
            "href": r.get("href", r.get("url", "")),
            "body": r.get("body", r.get("snippet", "")),
        }
        for r in raw
    ]


# ---------------------------------------------------------------------------
# Стратегия 3: DDG HTML (curl_cffi)
# ---------------------------------------------------------------------------


async def _search_ddg_html(query: str, max_results: int) -> list[dict[str, str]]:
    """Прямой парсинг html.duckduckgo.com/html/ через curl_cffi."""
    from bs4 import BeautifulSoup
    from curl_cffi import requests

    def _do() -> str:
        session: requests.Session[Any] = requests.Session()
        resp = session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "ia": "web"},
            headers=_HEADERS,
            impersonate="chrome",
            timeout=20,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return str(resp.text)

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
        # DDG отдаёт редирект /l/?uddg=<url> — извлекаем целевой URL.
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
# Стратегия 4: SearXNG
# ---------------------------------------------------------------------------


async def _search_searx(query: str, max_results: int) -> list[dict[str, str]]:
    """SearXNG публичные инстансы (best-effort)."""
    from curl_cffi import requests

    last_err: Exception | None = None

    def _fetch(base: str) -> dict[str, Any]:
        session: requests.Session[Any] = requests.Session()
        resp = session.get(
            f"{base}/search",
            params={"q": query, "format": "json"},
            headers=_HEADERS,
            impersonate="chrome",
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"{base} HTTP {resp.status_code}")
        data: dict[str, Any] = resp.json()
        return data

    for base in _SEARX_INSTANCES:
        try:
            data = await asyncio.to_thread(_fetch, base)
            raw = data.get("results") or []
            results = [
                {
                    "title": r.get("title", ""),
                    "href": r.get("url", ""),
                    "body": r.get("content", ""),
                }
                for r in raw[:max_results]
            ]
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
        "Ищет АКТУАЛЬНУЮ информацию в интернете. "
        "ОБЯЗАТЕЛЬНО вызывай при вопросах про новости, версии ПО, релизы, "
        "цены, законы, курсы валют — то, что могло измениться после обучения "
        "модели. НЕ отправляй в запрос фрагменты кода, пути, имена внутренних "
        "модулей, хостов, ключей/токенов — это утечка."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
    capabilities=["net"],
    timeout_s=90.0,  # 4 стратегии по 20-25с
)
async def web_search(
    query: str,
    max_results: int = 5,
    search_cache: SearchCache | None = None,
    security: SecurityPolicy | None = None,
) -> str:
    """Ищет в интернете с hygiene-фильтром.

    Args:
        query: Поисковый запрос (будет очищен от чувствительных данных).
        max_results: Количество результатов (1-10).
        search_cache: Кэш запросов (пробрасывается автоматически).
        security: Политика безопасности проекта (default — NEUTRAL_ONLY,
            redaction on). Передаётся из ChatPanel/Orchestrator'а после
            загрузки config v3.

    Returns:
        Отформатированные результаты в ``<untrusted>`` или сообщение об
        ошибке/отказе.
    """
    if not query or not query.strip():
        return "Ошибка: пустой поисковый запрос."

    sec = security or SecurityPolicy()
    mode = sec.effective_web_mode()

    if mode is NetworkMode.OFF:
        return (
            "<untrusted>\n"
            "web_search отключён политикой безопасности проекта "
            "(confidential/local_only-off). Используй только локальные данные.\n"
            "</untrusted>"
        )

    clean, hits = filter_query(query, mode=mode, policy=sec)
    if hits:
        logger.warning(f"web_search hygiene: redacted categories={hits} query={clean[:80]!r}")
    if not clean.strip():
        return (
            "<untrusted>\n"
            "Запрос отклонён hygiene-фильтром: содержит только чувствительные "
            "данные (пути/идентификаторы/имена модулей) или не прошёл проверку "
            "на нейтральность. Переформулируй вопрос в терминах технологий/"
            "кодов ошибок.\n"
            "</untrusted>"
        )

    return await _web_search_impl(clean, max_results, search_cache)


async def _web_search_impl(
    query: str,
    max_results: int,
    search_cache: SearchCache | None,
) -> str:
    """Реализация поиска (Yandex → DDGS → DDG HTML → SearX)."""
    if search_cache:
        cached = search_cache.get(query)
        if cached:
            return cached

    try:
        n = max(1, min(int(max_results), 10))
    except (ValueError, TypeError):
        n = 5

    strategies = [
        ("Yandex", _search_yandex),
        ("DDGS", _search_ddgs),
        ("DDG HTML", _search_ddg_html),
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
                logger.info(f"web_search: '{query}' via {name}, {len(results)} results")
                if search_cache:
                    search_cache.set(query, text)
                return "<untrusted>\n" + text + "\n</untrusted>"
            errors.append(f"{name}: no results")
        except Exception as e:
            logger.warning(f"web_search {name} failed: {type(e).__name__}: {e}")
            errors.append(f"{name}: {type(e).__name__}")

    logger.error(f"web_search: all strategies failed for '{query}': {errors}")
    return (
        f"<untrusted>\nНе удалось найти информацию по запросу «{query}». "
        f"Все стратегии поиска вернули ошибку. Попробуйте переформулировать "
        f"запрос.\n</untrusted>"
    )
