"""Провайдеры поиска (2.13): единый протокол + 5 реализаций.

Каждый провайдер — тонкая async-обёртка над своим источником:

  * `SearXNGProvider` — локальный SearXNG (мета-поиск по 20+ движкам).
  * `OpenSERPProvider` — локальный OpenSERP (бесплатный API к Google/Yandex/…).
  * `YandexSearchAPIProvider` — официальный Yandex Search API (платный).
  * `OllamaWebSearchProvider` — Ollama Web Search (опционально).
  * `DDGProvider` — DuckDuckGo (curl_cffi + HTML-парсинг, последний fallback).

Провайдер НЕ решает, использовать ли его — это делает
`tools/web_search.py` (цепочка с fallback). Провайдер только:
  1. Проверяет, что он «настроен» (`is_available()`).
  2. Делает запрос и возвращает `list[SearchResult]` или бросает
     `SearchProviderError`.

Падение провайдера (сеть, 5xx, капча) — не катастрофа: цепочка перейдёт
к следующему.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx
from loguru import logger

from core.config import SearchConfig


class SearchProviderError(Exception):
    """Ошибка провайдера (сеть, капча, 5xx). Цепочка ловит и идёт дальше."""


@dataclass(frozen=True)
class SearchResult:
    """Один результат поиска."""

    title: str
    href: str
    body: str = ""

    def render(self, index: int) -> str:
        """Форматирует для observation."""
        return f"{index}. {self.title}\n   {self.href}\n   {self.body[:400]}"


@runtime_checkable
class SearchProvider(Protocol):
    """Контракт провайдера (2.13)."""

    name: str

    async def search(self, query: str, num: int = 10) -> list[SearchResult]:
        """Возвращает результаты или бросает SearchProviderError."""
        ...

    def is_available(self) -> bool:
        """``True``, если провайдер настроен (URL/ключ присутствуют)."""
        ...


# ---------------------------------------------------------------------------
# SearXNG
# ---------------------------------------------------------------------------


class SearXNGProvider:
    """Локальный SearXNG (`http://localhost:8080`)."""

    name = "searxng"

    def __init__(self, base_url: str, timeout_s: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        return bool(self.base_url)

    async def search(self, query: str, num: int = 10) -> list[SearchResult]:
        url = f"{self.base_url}/search"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={"User-Agent": "OlliDesk/0.1", "Accept": "application/json"},
            ) as client:
                resp = await client.get(
                    url,
                    params={"q": query, "format": "json", "categories": "general"},
                )
                if resp.status_code != 200:
                    raise SearchProviderError(f"SearXNG HTTP {resp.status_code}")
                data = resp.json()
        except SearchProviderError:
            raise
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise SearchProviderError(f"SearXNG network: {e}") from e
        except Exception as e:
            raise SearchProviderError(f"SearXNG: {e}") from e

        raw = data.get("results") or []
        results: list[SearchResult] = []
        for r in raw[:num]:
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "").strip()
            href = str(r.get("url") or r.get("href") or "").strip()
            if title and href:
                results.append(
                    SearchResult(
                        title=title,
                        href=href,
                        body=str(r.get("content") or r.get("snippet") or "")[:400],
                    )
                )
        return results


# ---------------------------------------------------------------------------
# OpenSERP
# ---------------------------------------------------------------------------


class OpenSERPProvider:
    """Локальный OpenSERP (`http://localhost:7000`).

    Поддерживает несколько движков; по умолчанию `google` — самый
    широкий. Конфиг провайдера — только URL, движок фиксирован.
    """

    name = "openserp"

    def __init__(self, base_url: str, timeout_s: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        return bool(self.base_url)

    async def search(self, query: str, num: int = 10) -> list[SearchResult]:
        # OpenSERP API: GET /google/search?text=<q>&limit=<n>
        url = f"{self.base_url}/google/search"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={"User-Agent": "OlliDesk/0.1"},
            ) as client:
                resp = await client.get(url, params={"text": query, "limit": num})
                if resp.status_code != 200:
                    raise SearchProviderError(f"OpenSERP HTTP {resp.status_code}")
                data = resp.json()
        except SearchProviderError:
            raise
        except httpx.HTTPError as e:
            raise SearchProviderError(f"OpenSERP network: {e}") from e
        except Exception as e:
            raise SearchProviderError(f"OpenSERP: {e}") from e

        # Формат ответа: {"results": [{"title", "url", "description"}, …]}
        raw = data.get("results") or data.get("data") or []
        results: list[SearchResult] = []
        for r in raw[:num]:
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "").strip()
            href = str(r.get("url") or r.get("href") or "").strip()
            if title and href:
                results.append(
                    SearchResult(
                        title=title,
                        href=href,
                        body=str(r.get("description") or r.get("snippet") or "")[:400],
                    )
                )
        return results


# ---------------------------------------------------------------------------
# Yandex Search API
# ---------------------------------------------------------------------------


class YandexSearchAPIProvider:
    """Официальный Yandex Search API (платный).

    Документация: `https://yandex.cloud/ru/docs/search-api/`.
    Требует:
      * ``yandex_api_key`` — IAM-токен или API-ключ сервисного аккаунта.
      * ``yandex_folder_id`` — folder ID проекта в Yandex Cloud.
    """

    name = "yandex"

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        timeout_s: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.folder_id = folder_id
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        return bool(self.api_key and self.folder_id)

    async def search(self, query: str, num: int = 10) -> list[SearchResult]:
        url = "https://searchapi.api.cloud.yandex.net/v2/web/search"
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": {
                "searchType": "SEARCH_TYPE_RU",
                "queryText": query,
                "familyMode": "FAMILY_MODE_NONE",
                "page": 0,
            },
            "folderId": self.folder_id,
            "responseFormat": "FORMAT_XML",
            "getPartialResults": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 403:
                    raise SearchProviderError("Yandex API: 403 (проверьте ключ и folder_id)")
                if resp.status_code != 200:
                    raise SearchProviderError(f"Yandex API HTTP {resp.status_code}")
                data = resp.json()
        except SearchProviderError:
            raise
        except httpx.HTTPError as e:
            raise SearchProviderError(f"Yandex API network: {e}") from e
        except Exception as e:
            raise SearchProviderError(f"Yandex API: {e}") from e

        return self._parse_yandex_xml(data, num)

    @staticmethod
    def _parse_yandex_xml(data: dict, num: int) -> list[SearchResult]:
        """Парсит ``responseFormat=XML`` (base64 XML-строка).

        Пакет возвращает ``rawData`` — base64-encoded XML. Парсим через
        `xml.etree.ElementTree`.
        """
        import base64
        import xml.etree.ElementTree as ET

        raw = data.get("rawData") or ""
        if not raw:
            return []
        try:
            xml_bytes = base64.b64decode(raw)
        except Exception:
            return []
        results: list[SearchResult] = []
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return []
        # XPath зависит от `yandexsearch`. Упрощённо — берём все `doc`.
        for doc in root.iter():
            if doc.tag.endswith("doc"):
                title = ""
                href = ""
                body = ""
                for child in doc:
                    tag = child.tag.split("}", 1)[-1]
                    if tag == "url":
                        href = (child.text or "").strip()
                    elif tag == "title":
                        title = "".join(child.itertext()).strip()
                    elif tag == "passages":
                        body = "".join(child.itertext()).strip()
                if title and href:
                    results.append(SearchResult(title=title, href=href, body=body[:400]))
                if len(results) >= num:
                    break
        return results


# ---------------------------------------------------------------------------
# Ollama Web Search
# ---------------------------------------------------------------------------


class OllamaWebSearchProvider:
    """Ollama Web Search (`https://ollama.com/api/web_search`).

    По документации Ollama возвращает JSON с полями ``results``.
    """

    name = "ollama"

    def __init__(self, api_key: str, timeout_s: float = 20.0) -> None:
        self.api_key = api_key
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, num: int = 10) -> list[SearchResult]:
        url = "https://ollama.com/api/web_search"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(url, headers=headers, json={"query": query, "max_results": num})
                if resp.status_code == 401:
                    raise SearchProviderError("Ollama Web Search: 401 (неверный api_key)")
                if resp.status_code != 200:
                    raise SearchProviderError(f"Ollama Web Search HTTP {resp.status_code}")
                data = resp.json()
        except SearchProviderError:
            raise
        except httpx.HTTPError as e:
            raise SearchProviderError(f"Ollama Web Search network: {e}") from e
        except Exception as e:
            raise SearchProviderError(f"Ollama Web Search: {e}") from e

        raw = data.get("results") or []
        results: list[SearchResult] = []
        for r in raw[:num]:
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "").strip()
            href = str(r.get("url") or r.get("href") or "").strip()
            if title and href:
                results.append(
                    SearchResult(
                        title=title,
                        href=href,
                        body=str(r.get("content") or r.get("snippet") or "")[:400],
                    )
                )
        return results


# ---------------------------------------------------------------------------
# DDG (последний fallback)
# ---------------------------------------------------------------------------


class DDGProvider:
    """DuckDuckGo через `ddgs`.

    Не требует настройки. В условиях РФ может отдавать капчу; поэтому —
    последний в цепочке.
    """

    name = "ddg"

    def __init__(self, timeout_s: float = 20.0) -> None:
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        try:
            import ddgs  # noqa: F401

            return True
        except ImportError:
            return False

    async def search(self, query: str, num: int = 10) -> list[SearchResult]:
        try:
            from ddgs import DDGS
        except ImportError as e:
            raise SearchProviderError("ddgs не установлен: `pip install ddgs`") from e

        def _do() -> list[dict]:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=num, region="ru-ru"))

        try:
            raw = await asyncio.wait_for(asyncio.to_thread(_do), timeout=self.timeout_s)
        except TimeoutError as e:
            raise SearchProviderError(f"DDG timeout ({self.timeout_s}s)") from e
        except Exception as e:
            raise SearchProviderError(f"DDG: {e}") from e

        results: list[SearchResult] = []
        for r in raw[:num]:
            title = str(r.get("title") or "").strip()
            href = str(r.get("href") or r.get("url") or "").strip()
            if title and href:
                results.append(
                    SearchResult(
                        title=title,
                        href=href,
                        body=str(r.get("body") or r.get("snippet") or "")[:400],
                    )
                )
        return results


# ---------------------------------------------------------------------------
# Factory: собрать цепочку из конфига
# ---------------------------------------------------------------------------


def build_chain(config: SearchConfig) -> list[SearchProvider]:
    """Собирает цепочку провайдеров по `SearchConfig.providers`.

    Провайдеры, которые не настроены (`is_available() == False`),
    пропускаются с debug-логом. Если ни один не доступен — возвращается
    пустой список (вызывающий решает, что делать).

    Args:
        config: `SearchConfig` из AppConfig.

    Returns:
        Список провайдеров в порядке приоритета (только доступные).
    """
    factory: dict[str, Any] = {
        "searxng": lambda: SearXNGProvider(config.searxng_url, timeout_s=float(config.timeout_s)),
        "openserp": lambda: OpenSERPProvider(config.openserp_url, timeout_s=float(config.timeout_s)),
        "yandex": lambda: YandexSearchAPIProvider(
            config.yandex_api_key,
            config.yandex_folder_id,
            timeout_s=float(config.timeout_s),
        ),
        "ollama": lambda: OllamaWebSearchProvider(config.ollama_api_key, timeout_s=float(config.timeout_s)),
        "ddg": lambda: DDGProvider(timeout_s=float(config.timeout_s)),
    }

    chain: list[SearchProvider] = []
    for name in config.providers:
        make = factory.get(name)
        if make is None:
            logger.warning(f"search: неизвестный провайдер {name!r}")
            continue
        try:
            provider = make()
        except Exception as e:
            logger.debug(f"search: {name} не создан: {e}")
            continue
        if not provider.is_available():
            logger.debug(f"search: {name} недоступен (не настроен) — пропускаю")
            continue
        chain.append(provider)

    logger.debug(f"search chain: {[p.name for p in chain]} ({len(chain)} из {len(config.providers)})")
    return chain


async def shutdown_providers(chain: list[SearchProvider]) -> None:
    """Заглушка на будущее (провайдеры используют короткоживущие клиенты)."""
    for p in chain:
        close = getattr(p, "aclose", None)
        if close:
            with contextlib.suppress(Exception):
                res = close()
                if asyncio.iscoroutine(res):
                    await res


__all__ = [
    "DDGProvider",
    "OllamaWebSearchProvider",
    "OpenSERPProvider",
    "SearXNGProvider",
    "SearchProvider",
    "SearchProviderError",
    "SearchResult",
    "YandexSearchAPIProvider",
    "build_chain",
    "shutdown_providers",
]
