"""Инструмент поиска по кодовой базе через RAG."""

from datetime import date, datetime, time
from typing import Any

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy


def _parse_date(value: str) -> float:
    """Преобразует ISO-дату ``YYYY-MM-DD`` в unix-время начала дня.

    Raises:
        ValueError: Если строка не является датой в формате ISO.
    """
    return datetime.combine(date.fromisoformat(value), time.min).timestamp()


def _end_of_day(value: str) -> float:
    """Преобразует ISO-дату ``YYYY-MM-DD`` в unix-время конца дня (включительно)."""
    return datetime.combine(date.fromisoformat(value), time.max).timestamp()


@tool(
    name="search_codebase",
    description=(
        "Ищет информацию в кодовой базе по текстовому запросу через векторный поиск. "
        "Можно сузить поиск языком программирования, каталогом (префиксом пути) "
        "и датами изменения файлов (modified_after/modified_before, формат YYYY-MM-DD)."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
async def search_codebase(
    query: str,
    n_results: int = 5,
    language: str | None = None,
    path: str | None = None,
    modified_after: str | None = None,
    modified_before: str | None = None,
    vector_store: Any = None,
) -> str:
    """Ищет релевантные фрагменты кода по запросу.

    Args:
        query: Текст запроса
        n_results: Количество результатов (max 10)
        language: Фильтр по языку (python, javascript, typescript, html, css, markdown, json, yaml)
        path: Ограничить поиск каталогом/префиксом пути (например, ``agents`` или ``ui/chat``)
        modified_after: Только файлы, изменённые не раньше этой даты (``YYYY-MM-DD``)
        modified_before: Только файлы, изменённые не позже этой даты (``YYYY-MM-DD``)
        vector_store: Экземпляр VectorStore (передаётся через context)

    Returns:
        Найденные фрагменты кода
    """
    if vector_store is None:
        return "Векторное хранилище не подключено. Откройте проект для индексации."

    try:
        after = _parse_date(modified_after) if modified_after else None
        before = _end_of_day(modified_before) if modified_before else None
    except ValueError as e:
        return f"Ошибка: неверный формат даты ({e}). Ожидается YYYY-MM-DD."

    try:
        n = min(max(n_results, 1), 10)
        results = await vector_store.search(
            query,
            n_results=n,
            language=language or None,
            path_prefix=path or None,
            modified_after=after,
            modified_before=before,
        )

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
