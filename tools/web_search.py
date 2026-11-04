"""Инструмент поиска в интернете (2.13: цепочка провайдеров с fallback).

Стратегии отделены в `agents/search_providers.py`. Здесь — тонкая обёртка:

  1. `web_search_hygiene.filter_query` — очистка от чувствительного.
  2. `build_chain` — собирает доступных провайдеров из `SearchConfig`.
  3. Идём по цепочке, пока один не вернёт результат.
  4. `EventBus.emit("search.provider.used")` — какой сработал.
  5. Кэш `SearchCache` — не платим дважды.
  6. Каждый результат обёрнут в `<untrusted>…</untrusted>`.
"""

from __future__ import annotations

import time

from loguru import logger

from agents.search_providers import (
    SearchProviderError,
    SearchResult,
    build_chain,
)
from agents.tool_registry import tool
from core.config import AgentMode, SearchConfig, ToolPolicy
from core.event_bus import emit_event
from core.events import EventKind
from core.search_cache import SearchCache
from core.security import NetworkMode, SecurityPolicy
from tools.web_search_hygiene import filter_query


def _get_search_config() -> SearchConfig:
    """Читает `SearchConfig` из текущего конфига (fallback на дефолт)."""
    try:
        from core.config import load_config

        return load_config().search
    except Exception:
        return SearchConfig()


def _render_results(results: list[SearchResult], provider: str) -> str:
    """Форматирует результаты с указанием провайдера."""
    parts = [f"Провайдер: {provider}", ""]
    for i, r in enumerate(results, 1):
        parts.append(r.render(i))
    return "\n\n".join(parts)


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
    timeout_s=120.0,  # 5 провайдеров × timeout_s
)
async def web_search(
    query: str,
    max_results: int = 5,
    search_cache: SearchCache | None = None,
    security: SecurityPolicy | None = None,
) -> str:
    """Ищет в интернете с hygiene-фильтром и цепочкой провайдеров.

    Args:
        query: Поисковый запрос (будет очищен от чувствительных данных).
        max_results: Количество результатов (1-10).
        search_cache: Кэш запросов (пробрасывается автоматически).
        security: Политика безопасности проекта.

    Returns:
        Отформатированные результаты в ``<untrusted>`` или сообщение
        об ошибке/отказе.
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
        logger.warning(
            f"web_search hygiene: redacted categories={hits} "
            f"query={clean[:80]!r}"
        )
    if not clean.strip():
        return (
            "<untrusted>\n"
            "Запрос отклонён hygiene-фильтром: содержит только чувствительные "
            "данные (пути/идентификаторы/имена модулей) или не прошёл проверку "
            "на нейтральность. Переформулируй вопрос в терминах технологий/"
            "кодов ошибок.\n"
            "</untrusted>"
        )

    # Кэш — до похода в сеть.
    cache_ttl = _get_search_config().cache_ttl_hours
    if search_cache:
        cached = search_cache.get(clean, max_age_hours=cache_ttl)
        if cached:
            logger.debug(f"web_search: cache hit для {clean[:60]!r}")
            return cached

    try:
        n = max(1, min(int(max_results), 10))
    except (ValueError, TypeError):
        n = 5

    chain = build_chain(_get_search_config())
    if not chain:
        return (
            "<untrusted>\n"
            "Не настроен ни один поисковый провайдер. Проверьте "
            "`search.providers` в config.yaml: нужен хотя бы один из "
            "searxng/openserp/yandex/ollama/ddg (с URL/ключом).\n"
            "</untrusted>"
        )

    attempted: list[str] = []
    errors: list[str] = []
    for provider in chain:
        attempted.append(provider.name)
        started = time.perf_counter()
        try:
            results = await provider.search(clean, n)
        except SearchProviderError as e:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.warning(
                f"web_search: {provider.name} failed за {duration_ms} ms: {e}"
            )
            errors.append(f"{provider.name}: {e}")
            continue
        except Exception as e:
            logger.exception(
                f"web_search: {provider.name} упал с исключением"
            )
            errors.append(f"{provider.name}: {type(e).__name__}: {e}")
            continue

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        emit_event(
            EventKind.SEARCH_PROVIDER_USED.value,
            {
                "provider": provider.name,
                "duration_ms": duration_ms,
                "n_results": len(results),
            },
        )

        if results:
            text = _render_results(results, provider.name)
            wrapped = "<untrusted>\n" + text + "\n</untrusted>"
            logger.info(
                f"web_search: '{clean[:60]}' via {provider.name}, "
                f"{len(results)} results за {duration_ms} ms"
            )
            if search_cache:
                search_cache.set(clean, wrapped)
            return wrapped

        logger.info(
            f"web_search: {provider.name} вернул 0 результатов за {duration_ms} ms"
        )
        errors.append(f"{provider.name}: 0 results")

    # Все провайдеры упали или вернули 0.
    emit_event(
        EventKind.SEARCH_CHAIN_EXHAUSTED.value,
        {
            "query": clean[:120],
            "attempted": attempted,
            "errors": errors,
        },
    )
    return (
        "<untrusted>\n"
        f"Не удалось найти информацию по запросу «{clean}». "
        f"Провайдеры: {', '.join(attempted) or '—'}. "
        f"Ошибки: {'; '.join(errors[:5])}. "
        f"Попробуйте переформулировать запрос.\n"
        "</untrusted>"
    )


__all__ = ["web_search"]