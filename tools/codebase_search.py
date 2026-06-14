"""Инструмент поиска по кодовой базе через RAG."""

from typing import Any

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy


@tool(
    name="search_codebase",
    description="Ищет информацию в кодовой базе по текстовому запросу через векторный поиск",
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
)
async def search_codebase(query: str, n_results: int = 5, vector_store: Any = None) -> str:
    """Ищет релевантные фрагменты кода по запросу.

    Args:
        query: Текст запроса
        n_results: Количество результатов (max 10)
        vector_store: Экземпляр VectorStore (передаётся через context)

    Returns:
        Найденные фрагменты кода
    """
    if vector_store is None:
        return "Векторное хранилище не подключено. Откройте проект для индексации."

    try:
        n = min(max(n_results, 1), 10)
        results = await vector_store.search(query, n_results=n)

        if not results:
            return "Ничего не найдено по запросу."

        parts = []
        for i, r in enumerate(results, 1):
            parts.append(
                f"Результат {i}: {r.file_path} "
                f"(строки {r.chunk.start_line}-{r.chunk.end_line})\n"
                f"```\n{r.chunk.content}\n```"
            )

        logger.debug(f"Поиск по коду: {query} -> {len(results)} результатов")
        return "\n\n".join(parts)
    except Exception as e:
        logger.error(f"Ошибка поиска по коду: {e}")
        return f"Ошибка поиска: {e}"
