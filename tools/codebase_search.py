"""Инструмент поиска по кодовой базе через RAG."""

from datetime import date, datetime, time
from typing import Any

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy

# Алиасы языков: модель часто пишет «C#», «js», «csharp» — приводим к тому
# виду, который кладёт `fs.indexer._detect_language` в метаданные чанка.
_LANGUAGE_ALIASES: dict[str, str] = {
    # C# / .NET
    "c#": "csharp",
    "csharp": "csharp",
    "cs": "csharp",
    ".cs": "csharp",
    "csproj": "msbuild",
    "msbuild": "msbuild",
    "sln": "solution",
    "solution": "solution",
    # C/C++
    "c++": "cpp",
    "cpp": "cpp",
    "cplusplus": "cpp",
    # JS/TS
    "js": "javascript",
    "javascript": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    # Python
    "py": "python",
    "python": "python",
    "python3": "python",
    # Shell
    "sh": "shell",
    "bash": "shell",
    "shell": "shell",
    # YAML
    "yml": "yaml",
    "yaml": "yaml",
    # Markdown
    "md": "markdown",
    "markdown": "markdown",
    # Прочее
    "html": "html",
    "css": "css",
    "json": "json",
    "xml": "xml",
    "go": "go",
    "golang": "go",
    "rs": "rust",
    "rust": "rust",
    "java": "java",
    "kt": "kotlin",
    "kotlin": "kotlin",
    "rb": "ruby",
    "ruby": "ruby",
    "php": "php",
    "swift": "swift",
    "dart": "dart",
    "sql": "sql",
    "toml": "toml",
    "ini": "ini",
    "vue": "vue",
    "svelte": "svelte",
    "astro": "astro",
    # === IaC + Ansible/Vagrant ===
    "hcl": "hcl",
    "tf": "terraform",
    "terraform": "terraform",
    "tfvars": "terraform-vars",
    "terraform-vars": "terraform-vars",
    "pkr.hcl": "hcl",
    "vagrant": "vagrant",
    "vagrantfile": "vagrant",
    "ansible": "ansible",
}


def _normalize_language(language: str | None) -> str | None:
    """Приводит входной язык к каноническому имени из метаданных чанка."""
    if not language:
        return None
    key = language.strip().lower()
    return _LANGUAGE_ALIASES.get(key, key)


def _known_languages() -> str:
    """Список канонических имён — для подсказки модели."""
    return ", ".join(sorted(set(_LANGUAGE_ALIASES.values())))


def _parse_date(value: str) -> float:
    """Преобразует ISO-дату ``YYYY-MM-DD`` в unix-время начала дня."""
    return datetime.combine(date.fromisoformat(value), time.min).timestamp()


def _end_of_day(value: str) -> float:
    """Преобразует ISO-дату ``YYYY-MM-DD`` в unix-время конца дня."""
    return datetime.combine(date.fromisoformat(value), time.max).timestamp()


@tool(
    name="search_codebase",
    description=(
        "Ищет информацию в кодовой базе по текстовому запросу через векторный поиск. "
        "Можно сузить поиск языком программирования (python, csharp, javascript, "
        "typescript, go, rust, …), каталогом (префиксом пути) и датами изменения "
        "файлов (modified_after/modified_before, формат YYYY-MM-DD)."
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
    """Ищет релевантные фрагменты кода по запросу."""
    if vector_store is None:
        return "Векторное хранилище не подключено. Откройте проект для индексации."

    # Нормализуем язык ДО обращения к store — иначе «C#» не совпадёт
    # с «csharp» в метаданных чанка и поиск вернёт пустоту.
    normalized_language = _normalize_language(language)

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
            language=normalized_language,
            path_prefix=path or None,
            modified_after=after,
            modified_before=before,
        )

        if not results:
            # Диагностическое сообщение: подсказываем, какие значения
            # фильтра точно сработают, и что запрос вообще отработал.
            hint = ""
            if normalized_language:
                hint = (
                    f" (фильтр language={normalized_language!r} — доступные значения: {_known_languages()})"
                )
            return (
                f"Ничего не найдено по запросу{hint}. "
                "Попробуй переформулировать запрос (например, конкретное имя "
                "класса/функции) или убрать фильтр языка."
            )

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
