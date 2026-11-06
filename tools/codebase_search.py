"""Инструмент поиска по кодовой базе через RAG."""

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from core.sandbox import is_denied_file

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
    capabilities=["fs.read"],
    timeout_s=120.0,  # RAG+rerank на слабом CPU может быть долгим
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

        # 0.1.2: не отдавать чанки из секретных файлов (.env, *.pem, …).
        # Индексатор по умолчанию их и так не индексирует (ignored_patterns),
        # но пользователь мог убрать паттерн — фильтруем на всякий случай.
        results = [r for r in results if not is_denied_file(Path(r.file_path).name)]

        if not results:
            return "Ничего не найдено (секретные файлы исключены политикой безопасности)."
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


@tool(
    name="find_references",
    description=(
        "Ищет использования символа (класс/функция/метод) в коде проекта. "
        "Использует LSP (если сервер языка установлен) или RAG-поиск по "
        "имени как fallback.\n\n"
        "Примеры:\n"
        "  find_references(symbol='send_message')\n"
        "  find_references(symbol='Config', file='core/config.py', line=42)\n\n"
        "Возвращает список вхождений с путями и строками. Если LSP "
        "недоступен — помечает результат «эвристика»."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
    capabilities=["fs.read"],
    timeout_s=60.0,
)
async def find_references(
    symbol: str,
    file: str | None = None,
    line: int = 0,
    vector_store: Any = None,
    project_root: str = "",
) -> str:
    """Ищет использования символа.

    Args:
        symbol: Имя символа (``send_message``, ``Config``, …).
        file: Файл, где символ объявлен (для LSP-precise-запроса).
            ``None`` → только эвристика.
        line: Строка объявления (1-based). ``0`` → эвристика.
        vector_store: RAG-store (инжектится).
        project_root: Корень проекта (для LSP).

    Returns:
        Текст с найденными вхождениями или сообщение об отсутствии.
    """
    if not symbol or not symbol.strip():
        return "Ошибка: пустое имя символа."
    symbol = symbol.strip()
    logger.debug(f"find_references: symbol={symbol!r}, file={file!r}, line={line}")

    # 1. Пробуем LSP (precise).
    if file and line > 0:
        try:
            from agents.lsp_bridge import LspBridge

            bridge = LspBridge._get_default()
            if bridge is not None:
                from pathlib import Path as _Path

                fpath = _Path(file)
                language = None
                if not fpath.is_absolute() and project_root:
                    fpath = _Path(project_root) / fpath
                # LSP 0-based: line-1, character=0 (начало строки).
                refs = await _try_lsp_references(bridge, fpath, line - 1, language)
                if refs:
                    return _format_lsp_references(symbol, refs)
        except Exception as e:
            logger.debug(f"find_references: LSP упал ({e}); fallback")

    # 2. Fallback: RAG-поиск по имени.
    if vector_store is None:
        return (
            f"LSP-сервер недоступен, а RAG не подключён — не могу найти "
            f"использования {symbol!r}. Открой проект для индексации."
        )
    try:
        results = await vector_store.search(symbol, n_results=15, path_prefix=None)
    except Exception as e:
        return f"Ошибка поиска: {e}"

    # Фильтруем секреты и пустые результаты.
    results = [
        r for r in (results or []) if not is_denied_file(Path(r.file_path).name) and symbol in r.chunk.content
    ]
    if not results:
        return (
            f"Эвристический поиск: {symbol!r} не найден. "
            f"(LSP-сервер не установлен; поиск по имени среди чанков.)"
        )

    lines: list[str] = [
        f"Эвристический поиск (LSP недоступен): {len(results)} вхождений {symbol!r}",
        "",
    ]
    for i, r in enumerate(results[:15], 1):
        # Найдём строку внутри чанка с символом для точной навигации.
        local_line = 0
        for offset, ln in enumerate(r.chunk.content.splitlines()):
            if symbol in ln:
                local_line = r.chunk.start_line + offset
                break
        lines.append(
            f"  {i}. {r.file_path}:{local_line or r.chunk.start_line} "
            f"— {r.chunk.content.strip().splitlines()[0][:120]}"
        )
    return "\n".join(lines)


async def _try_lsp_references(
    bridge: Any,
    file_path: Path,
    line: int,
    language: str | None,
) -> list[dict]:
    """Обёртка: LSP references через async-метод моста."""
    # LspBridge.references(language, file, line, col) — async.
    if language is None:
        from agents.lsp_bridge import _language_for_path

        language = _language_for_path(file_path) or ""
    if not language:
        return []
    try:
        return await bridge.references(
            language,
            str(file_path),
            line,
            0,
            project_root=str(file_path.parent),
        )
    except Exception as e:
        logger.debug(f"LSP references упал: {e}")
        return []


def _format_lsp_references(symbol: str, refs: list[dict]) -> str:
    """Форматирует LSP-ответ в текст для LLM."""
    lines = [f"LSP: {len(refs)} использований {symbol!r}", ""]
    for i, ref in enumerate(refs[:30], 1):
        uri = ref.get("uri") or ref.get("targetUri") or ""
        # file:///path → path
        path = uri.replace("file:///", "").replace("file://", "")
        rng = ref.get("range") or {}
        start = (rng.get("start") or {}).get("line", 0)
        lines.append(f"  {i}. {path}:{start + 1}")
    if len(refs) > 30:
        lines.append(f"  … ещё {len(refs) - 30}")
    return "\n".join(lines)
