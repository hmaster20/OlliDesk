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
        from duckduckgo_search import DDGS
    except ImportError:
        # fallback на старый метод (с предупреждением)
        return await _legacy_web_search(query, max_results)

    try:
        n = min(max(max_results, 1), 10)
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=n))
        if not results:
            return "Ничего не найдено."
        parts = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "Без заголовка")
            href = r.get("href", "")
            body = r.get("body", "")
            parts.append(f"{i}. {title}\n   {href}\n   {body[:300]}")
        return "\n\n".join(parts)
    except Exception as e:
        logger.error(f"Ошибка веб-поиска: {e}")
        return f"Ошибка поиска: {e}"
