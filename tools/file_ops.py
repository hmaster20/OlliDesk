"""Инструменты для чтения и записи файлов."""

import subprocess
import sys
from pathlib import Path

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from core.sandbox import SandboxedPathResolver  # NEW
from tools.git_ops import create_snapshot

_SKIP_NAMES = {
    ".git",
    ".svn",
    ".hg",
    ".vs",
    ".vscode",
    ".idea",
    ".gradle",
    ".next",
    ".nuxt",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    ".tox",
    "venv",
    ".venv",
    "env",
    ".env",
    "bin",
    "obj",
    "dist",
    "build",
    "target",
    "TestResults",
    "htmlcov",
    "coverage",
}

_MAX_ENTRIES = 200
_MAX_FILE_BYTES = 10 * 1024 * 1024

# Границы на одну порцию чтения. Нижняя граница — ЖЁСТКАЯ: если модель
# просит меньше, мы всё равно отдаём 4000 и говорим об этом. Иначе 7B-модель
# будет «безопасно» просить 1000 символов и читать файл 158 КБ за 158 итераций.
MIN_READ_CHUNK_CHARS = 4000
MAX_READ_FILE_CHUNK_CHARS = 30000
DEFAULT_READ_CHUNK_CHARS = 12000


def _format_size(num_bytes: int) -> str:
    """Человекочитаемый размер файла (B/KB/MB/GB)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _count_entries(directory: Path) -> int:
    """Число элементов в каталоге; при ошибке доступа — 0."""
    try:
        return sum(1 for _ in directory.iterdir())
    except OSError:
        return 0


@tool(
    name="read_file",
    description=(
        "Читает файл ПОРЦИЯМИ. Одна порция — до 30000 символов "
        "(по умолчанию 12000). НЕ ставь limit < 4000 — это тратит итерации "
        "впустую: файл 150 КБ по 1000 символов читается 150 вызовов и "
        "практически не заканчивается. Файл такого размера целиком не нужен: "
        "читай КЛЮЧЕВЫЕ фрагменты — начало (класс, поля), методы по имени "
        "(через search_codebase), конец. В конце ответа приходит хинт о "
        "продолжении. Пример: read_file(path='MainForm.cs', offset=12000)."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
def read_file(
    path: str,
    offset: int = 0,
    limit: int = DEFAULT_READ_CHUNK_CHARS,
    project_root: str = "",
) -> str:
    """Читает фрагмент файла.

    Args:
        path: Путь к файлу (абсолютный или относительный).
        offset: Смещение в символах от начала файла.
        limit: Сколько символов вернуть. Жёсткий диапазон
            [MIN_READ_CHUNK_CHARS, MAX_READ_FILE_CHUNK_CHARS].
        project_root: Корень проекта для проверки безопасности.

    Returns:
        Заголовок с диапазоном и размером, фрагмент, hint о продолжении.
    """
    # NEW: единый резолвер вместо дублированной `_resolve_path`.
    file_path = SandboxedPathResolver(project_root).resolve(path)
    if not file_path:
        return "Ошибка: путь вне допустимой директории"

    if not file_path.exists():
        return f"Файл не найден: {file_path}"

    if not file_path.is_file():
        return f"Это не файл: {file_path}"

    try:
        size_bytes = file_path.stat().st_size
    except OSError as e:
        return f"Ошибка доступа: {file_path} ({e})"

    if size_bytes > _MAX_FILE_BYTES:
        return (
            f"Файл слишком большой для read_file ({_format_size(size_bytes)}). "
            "Используй search_codebase или grep-подобный поиск."
        )

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return f"Ошибка доступа: {file_path}"
    except Exception as e:
        return f"Ошибка чтения файла: {e}"

    total = len(content)
    start = max(0, int(offset))

    # Жёсткая нормализация limit: если модель просит меньше минимума —
    # округляем до минимума. Иначе 7B-модель будет «безопасно» ползти по
    # 1000 символов и тратить 60 секунд TTFT на каждую итерацию.
    requested = int(limit)
    size = max(MIN_READ_CHUNK_CHARS, min(requested, MAX_READ_FILE_CHUNK_CHARS))
    limit_note = ""
    if requested < MIN_READ_CHUNK_CHARS:
        limit_note = f"\n⚠️ limit={requested} < минимума {MIN_READ_CHUNK_CHARS}; использовано limit={size}."
    elif requested > MAX_READ_FILE_CHUNK_CHARS:
        limit_note = (
            f"\n⚠️ limit={requested} > максимума {MAX_READ_FILE_CHUNK_CHARS}; использовано limit={size}."
        )

    if start >= total:
        return f"Смещение {start} за пределами файла {path} (всего {total} символов)."

    chunk = content[start : start + size]
    end = start + len(chunk)

    # Процент прочитанного — чтобы модель видела «прогресс» и не зацикливалась.
    percent = int(100 * end / total) if total > 0 else 100

    # Заголовок: путь, диапазон, всего символов, прогресс.
    header = f"{path} [символы {start}-{end} из {total}, {percent}%]{limit_note}"

    # Хинт на продолжение — в КОНЕЦ, чтобы он выжил при вынужденном
    # усечении в AgentLoop (там сохраняется хвост результата).
    if end < total:
        remaining = total - end
        # Если осталось много — настойчиво рекомендуем большой шаг или
        # search_codebase вместо перебора.
        if remaining > 2 * MAX_READ_FILE_CHUNK_CHARS:
            tail_hint = (
                f"\n\nПродолжение: read_file(path={path!r}, offset={end})."
                f"\n⚠️ Осталось {remaining} символов. НЕ читай файл целиком — "
                "используй search_codebase для поиска конкретных сущностей "
                "или пропусти середину."
            )
        else:
            tail_hint = (
                f"\n\nПродолжение: read_file(path={path!r}, offset={end}).\n… ещё {remaining} символов."
            )
    else:
        tail_hint = "\n\n(конец файла)"

    logger.debug(f"read_file: {file_path} [{start}:{end}] из {total}")
    return f"{header}\n\n{chunk}{tail_hint}"


@tool(
    name="list_directory",
    description=(
        "Показывает содержимое директории: подкаталоги и файлы с размерами. "
        "Служебные каталоги (bin, obj, node_modules, .git и т.п.) пропускаются. "
        "Путь '.' — корень проекта."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
def list_directory(path: str, project_root: str = "") -> str:
    """Возвращает содержимое директории в виде дерева одного уровня."""
    # NEW: единый резолвер вместо дублированной `_resolve_path`.
    dir_path = SandboxedPathResolver(project_root).resolve(path)
    if not dir_path:
        return "Ошибка: путь вне допустимой директории"

    if not dir_path.exists():
        return f"Директория не найдена: {dir_path}"

    if not dir_path.is_dir():
        return f"Это не директория: {dir_path}"

    try:
        dirs: list[str] = []
        files: list[str] = []
        skipped = 0
        for entry in sorted(dir_path.iterdir(), key=lambda p: p.name.lower()):
            if entry.name in _SKIP_NAMES:
                skipped += 1
                continue
            try:
                if entry.is_dir():
                    dirs.append(f"  {entry.name}/ — {_count_entries(entry)} элементов")
                else:
                    files.append(f"  {entry.name} — {_format_size(entry.stat().st_size)}")
            except OSError:
                (dirs if entry.is_dir() else files).append(f"  {entry.name}")

        total = len(dirs) + len(files)
        if total == 0:
            return f"Директория пуста: {dir_path}"

        lines = [f"Содержимое: {dir_path}"]
        shown = 0
        take_dirs = dirs[: max(_MAX_ENTRIES - shown, 0)]
        if take_dirs:
            lines.append("Каталоги:")
            lines.extend(take_dirs)
            shown += len(take_dirs)
        take_files = files[: max(_MAX_ENTRIES - shown, 0)]
        if take_files:
            lines.append("Файлы:")
            lines.extend(take_files)
            shown += len(take_files)
        if total > shown:
            lines.append(f"… показано {shown} из {total}")
        if skipped:
            lines.append(f"(служебных элементов пропущено: {skipped})")

        logger.debug(f"Прочитана директория: {dir_path} ({total} записей)")
        return "\n".join(lines)
    except PermissionError:
        return f"Ошибка доступа: {dir_path}"
    except Exception as e:
        return f"Ошибка чтения директории: {e}"


def _verify_python_syntax(file_path: Path) -> str:
    """Проверяет синтаксис .py-файла через ``python -m py_compile``.

    Используется как внешний верификатор после ``write_file`` (React.md 4.2):
    LLM-критик часто пропускает банальные опечатки (незакрытая скобка,
    лишний отступ), а ``py_compile`` ловит их детерминированно. Для не-``.py``
    файлов проверка отключена.

    Args:
        file_path: Путь к только что записанному файлу.

    Returns:
        Строка с текстом ошибки компиляции (stderr/stdout ``py_compile``)
        или пустая строка, если синтаксис корректен либо проверить нельзя.
    """
    if file_path.suffix.lower() != ".py":
        return ""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(file_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug(f"py_compile для {file_path} пропущен: {e}")
        return ""
    if result.returncode != 0:
        return (result.stderr or result.stdout or "unknown syntax error").strip()
    return ""

@tool(
    name="write_file",
    description="Записывает содержимое в файл. Перед записью создаёт Git-снапшот для отката.",
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
)
def write_file(
    path: str, content: str, project_root: str = ""
) -> str | dict[str, Any]:
    """Записывает содержимое в файл.

    [9.6.1] Возвращает `dict` со структурированным результатом для UI:
      * `_llm_text` — человекочитаемая строка для LLM (идёт в `content`);
      * `status` — "ok" | "no_change";
      * `path` — абсолютный путь;
      * `is_new_file` — файла не было до записи;
      * `original_content` — содержимое до записи (для отката);
      * `diff` — unified diff (для UI);
      * `lines_added`, `lines_removed` — статистика;
      * `syntax_error` — текст ошибки `py_compile` (пусто, если ок).

    Вне `ToolRegistry.execute` (прямой вызов) возвращает строку для
    обратной совместимости — но в проде всегда идёт через registry.
    """
    file_path = SandboxedPathResolver(project_root).resolve(path)
    if not file_path:
        return "Ошибка: путь вне допустимой директории"
    if file_path.is_dir():
        return f"Ошибка: {file_path} является директорией"

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # [9.6.1] Сохраняем оригинал ДО записи — для diff и отката.
        is_new_file = not file_path.exists()
        original_content = ""
        if not is_new_file:
            try:
                original_content = file_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except Exception:
                original_content = ""

        # Идемпотентность: если содержимое не изменилось — не пишем,
        # не создаём снапшот, возвращаем no_change.
        if not is_new_file and original_content == content:
            return {
                "_llm_text": f"Файл не изменился: {file_path.name}",
                "status": "no_change",
                "path": str(file_path),
                "is_new_file": False,
                "original_content": original_content,
                "written_content": content,
                "diff": "",
                "lines_added": 0,
                "lines_removed": 0,
                "syntax_error": "",
            }

        snapshot_status = None
        if project_root:
            repo_path = Path(project_root)
            try:
                snapshot_status = create_snapshot(
                    repo_path, message=f"Before agent write: {file_path.name}"
                )
            except Exception:
                snapshot_status = None

        file_path.write_text(content, encoding="utf-8")
        logger.info(f"Файл записан: {file_path} ({len(content)} символов)")

        # Внешний верификатор: py_compile для .py.
        syntax_error = _verify_python_syntax(file_path)

        # [9.6.1] Unified diff — для UI. difflib.unified_diff, без контекста
        # в заголовках (относительный путь вместо абсолютного — чище).
        import difflib

        original_lines = original_content.splitlines(keepends=True)
        new_lines = content.splitlines(keepends=True)
        diff_chunks = list(
            difflib.unified_diff(
                original_lines,
                new_lines,
                fromfile=f"a/{file_path.name}" if not is_new_file else "/dev/null",
                tofile=f"b/{file_path.name}",
                n=3,
            )
        )
        diff_text = "".join(diff_chunks)

        # Статистика по строкам.
        lines_added = sum(
            1 for ln in diff_chunks if ln.startswith("+") and not ln.startswith("+++")
        )
        lines_removed = sum(
            1 for ln in diff_chunks if ln.startswith("-") and not ln.startswith("---")
        )

        # [9.6.1] `_llm_text` — что видит модель. Добавляем хвост с
        # синтаксической ошибкой (если есть) — модель должна исправить.
        status_line = (
            "Файл создан" if is_new_file else "Файл успешно обновлён"
        )
        llm_text = f"{status_line}: {file_path.name}"
        if snapshot_status:
            llm_text += ". Создан Git-снапшот для отката."
        elif not project_root:
            llm_text += " (project_root не задан — без Git-снапшота)."
        else:
            llm_text += " (Git-снапшот не создан — репозиторий не найден или нет изменений)."
        if syntax_error:
            logger.warning(
                f"py_compile {file_path} выявил синтаксическую ошибку"
            )
            llm_text += (
                f"\n\n⚠️ Синтаксическая ошибка Python (py_compile):\n{syntax_error}"
            )

        return {
            "_llm_text": llm_text,
            "status": "ok",
            "path": str(file_path),
            "is_new_file": is_new_file,
            "original_content": original_content,
            # [9.8] Полное записанное содержимое — для Monaco DiffEditor
            # без повторного чтения с диска (файл мог измениться
            # параллельно, а diff должен показать ровно то, что записал агент).
            "written_content": content,
            "diff": diff_text,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "syntax_error": syntax_error,
        }
    except PermissionError:
        return f"Ошибка доступа при записи: {file_path}"
    except Exception as e:
        return f"Ошибка записи файла: {e}"

