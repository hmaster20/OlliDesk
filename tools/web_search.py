"""Инструмент поиска в интернете через DuckDuckGo."""

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy


@tool(
    name="web_search",
    description="Ищет информацию в интернете. "
    "Используйте для получения актуальных данных, документации, примеров кода.",
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
)
async def web_search(query: str, max_results: int = 5) -> str:
    """Ищет в интернете через DuckDuckGo.

    Args:
        query: Поисковый запрос
        max_results: Количество результатов (1-10)

    Returns:
        Результаты поиска
    """
    try:
        from duckduckgo_search import DDGS

        n = min(max(max_results, 1), 10)
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=n):
                results.append(r)

        if not results:
            return "Ничего не найдено."

        parts = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "Без заголовка")
            href = r.get("href", "")
            body = r.get("body", "")
            parts.append(f"{i}. {title}\n   {href}\n   {body[:300]}")

        logger.debug(f"Веб-поиск: {query} -> {len(results)} результатов")
        return "\n\n".join(parts)
    except ImportError:
        return "Модуль duckduckgo-search не установлен."
    except Exception as e:
        logger.error(f"Ошибка веб-поиска: {e}")
        return f"Ошибка поиска: {e}"
