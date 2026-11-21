"""Инструменты для чтения и записи файлов."""

import difflib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from core.sandbox import SandboxedPathResolver, is_denied_file

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
    capabilities=["fs.read"],
    timeout_s=15.0,
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
        limit: Сколько символов вернуть.
        project_root: Корень проекта для проверки безопасности.

    Returns:
        Заголовок с диапазоном, **sha256 (8 hex)** и **mtime (unix)**,
        фрагмент, hint о продолжении.

    Note:
        [10.9] sha256/mtime в заголовке нужны для stale check: перед
        ``apply_patch(expected_hash=...)`` модель передаёт хэш обратно,
        и инструмент сверяет его с текущим состоянием файла. Если файл
        изменился между чтением и правкой — apply_patch откажется.
    """
    # единый резолвер вместо дублированной `_resolve_path`.
    file_path = SandboxedPathResolver(project_root).resolve(path)
    if not file_path:
        # 0.1.2: различаем «вне песочницы» и «запрещённый секрет» —
        # модель должна понимать, что путь корректен, но политика
        # безопасности его блокирует.
        raw = str(path or "").strip()
        if raw and is_denied_file(Path(raw).name):
            return (
                f"Ошибка: файл '{Path(raw).name}' заблокирован политикой "
                f"безопасности (секрет). Не пытайся его читать."
            )
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

    # ... (без изменений до content)
    # 10.9: fingerprint для stale check.
    from tools.git_ops import file_fingerprint

    fp = file_fingerprint(file_path)
    header = (
        f"{path} [символы {start}-{end} из {total}, {percent}%]"
        f"{limit_note}\n"
        f"sha256={fp['hash'][:8]} mtime={fp['mtime']:.0f}"
    )

    # Заголовок: путь, диапазон, всего символов, прогресс.
    # header = f"{path} [символы {start}-{end} из {total}, {percent}%]{limit_note}"

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
    capabilities=["fs.read"],
    timeout_s=15.0,
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
            if entry.name in _SKIP_NAMES or is_denied_file(entry.name):
                skipped += 1
                continue
            try:
                if entry.is_dir():
                    dirs.append(f"  {entry.name}/ — {_count_entries(entry)} элементов")
                else:
                    files.append(f"  {entry.name} — {_format_size(entry.stat().st_size)}")
            except OSError:
                (dirs if entry.is_dir() else files).append(f"  {entry.name}")
            try:
                ...
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
    is_mutating=True,
    category="filesystem",
    capabilities=["fs.write"],
    timeout_s=30.0,
)
def write_file(path: str, content: str, project_root: str = "") -> str | dict[str, Any]:
    """Записывает содержимое в файл.

    [10.9] Перед записью создаёт файловый снапшот
    (``~/.ollidesk/snapshots/<id>/``). `snapshot_id` возвращается в
    метаданных и используется для кнопки «↩ Откатить» в UI-карточке.

    `git stash` больше НЕ используется: файл-снапшот не требует
    git-репозитория и восстанавливает даже новый файл (удаляет его).
    """
    file_path = SandboxedPathResolver(project_root).resolve(path, allow_sensitive=True)
    if not file_path:
        return "Ошибка: путь вне допустимой директории"
    if file_path.is_dir():
        return f"Ошибка: {file_path} является директорией"

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)

        is_new_file = not file_path.exists()
        original_content = ""
        if not is_new_file:
            try:
                original_content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                original_content = ""

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
                "snapshot_id": "",
            }

        # [10.9] Автоснапшот (всегда, если задан project_root).
        snapshot_id = ""
        if project_root:
            try:
                from tools.git_ops import create_file_snapshot

                snapshot_id = create_file_snapshot(
                    [file_path],
                    tool="write_file",
                    args_summary=f"{file_path.name} ({len(content)} chars)",
                    project_root=Path(project_root),
                )
            except Exception:
                logger.debug("write_file: снапшот не создан", exc_info=True)

        file_path.write_text(content, encoding="utf-8")
        logger.info(f"Файл записан: {file_path} ({len(content)} символов)")

        syntax_error = _verify_python_syntax(file_path)

        # --- [10.8] LSP-диагностика (если сервер доступен) ---
        lsp_errors: list[dict] = []
        try:
            from agents.lsp_bridge import LspBridge

            bridge = LspBridge._get_default()
            if bridge is not None:
                lsp_errors = bridge.diagnostics(file_path)
        except Exception:
            logger.debug("LSP diagnostics unavailable", exc_info=True)

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
        lines_added = sum(1 for ln in diff_chunks if ln.startswith("+") and not ln.startswith("+++"))
        lines_removed = sum(1 for ln in diff_chunks if ln.startswith("-") and not ln.startswith("---"))

        status_line = "Файл создан" if is_new_file else "Файл успешно обновлён"
        llm_text = f"{status_line}: {file_path.name}"
        if snapshot_id:
            llm_text += f". Снапшот: {snapshot_id}."
        if not project_root:
            llm_text += " (project_root не задан — без снапшота)."
        if syntax_error:
            llm_text += f"\n\n⚠️ Синтаксическая ошибка Python (py_compile):\n{syntax_error}"
        if lsp_errors:
            preview = "; ".join(
                f"{e.get('file', '?')}:{e.get('line', '?')} {e.get('message', '')[:120]}"
                for e in lsp_errors[:5]
            )
            llm_text += f"\n\n⚠️ LSP: {len(lsp_errors)} ошибок: {preview}"

        return {
            "_llm_text": llm_text,
            "status": "ok",
            "path": str(file_path),
            "is_new_file": is_new_file,
            "original_content": original_content,
            "written_content": content,
            "diff": diff_text,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "syntax_error": syntax_error,
            "lsp_errors": lsp_errors,
            "snapshot_id": snapshot_id,
        }
    except PermissionError:
        return f"Ошибка доступа при записи: {file_path}"
    except Exception as e:
        return f"Ошибка записи файла: {e}"


def _resolve_path(path: str, project_root: str = "", *, allow_sensitive: bool = False) -> Path | None:
    """Устаревшая обёртка для обратной совместимости тестов.

    Реализация — `core.sandbox.SandboxedPathResolver`. Оставлена, чтобы
    существующие тесты `tests/tools/test_file_ops.py` продолжали
    импортировать это имя. Новый код должен использовать резолвер напрямую.
    """
    return SandboxedPathResolver(project_root).resolve(path, allow_sensitive=allow_sensitive)


# ---------------------------------------------------------------------------
# 10.1: apply_patch — правка файла через unified diff
# ---------------------------------------------------------------------------

# Паттерн для hunk-заголовка: `@@ -1,3 +1,4 @@` или `@@ -1 +1 @@`.
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
)


@tool(
    name="apply_patch",
    description=(
        "Применяет unified diff к файлу. Используй для ПРАВОК существующих "
        "файлов вместо write_file (write_file перезаписывает файл целиком).\n\n"
        "Формат patch — стандартный unified diff (как в `git diff`):\n"
        "  --- a/file.py\n"
        "  +++ b/file.py\n"
        "  @@ -10,3 +10,3 @@\n"
        "   context\n"
        "  -old line\n"
        "  +new line\n"
        "   context\n\n"
        "Обязательно передавай expected_hash из read_file (sha256=... в "
        "заголовке). Если файл изменился между чтением и правкой — apply_patch "
        "откажется, чтобы не затереть чужие изменения."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
    is_mutating=True,
    category="filesystem",
    capabilities=["fs.write"],
    timeout_s=30.0,
)
def apply_patch(
    path: str,
    patch: str,
    expected_hash: str = "",
    project_root: str = "",
) -> str | dict[str, Any]:
    """Применяет unified diff к файлу.

    Args:
        path: Путь к файлу (относительно project_root).
        patch: Unified diff (может содержать `---/+++` заголовки, они
            игнорируются; важны hunk'и `@@ ... @@` и строки ` `, `-`, `+`).
        expected_hash: Ожидаемый sha256 файла (первые 8+ hex из read_file).
            Если задан и не совпадает с текущим — отказ (stale check).
            Пустая строка → stale check пропускается (не рекомендуется).
        project_root: Корень проекта.

    Returns:
        dict — как у write_file (с ``diff``, ``lines_added``, ``snapshot_id``,
        ``lsp_errors``), либо строка с ошибкой.
    """
    # 1. Резолв и проверки.
    file_path = SandboxedPathResolver(project_root).resolve(path, allow_sensitive=False)
    if not file_path:
        raw = str(path or "").strip()
        if raw and is_denied_file(Path(raw).name):
            return f"Ошибка: файл '{Path(raw).name}' заблокирован политикой безопасности (секрет)."
        return "Ошибка: путь вне допустимой директории"

    if not file_path.exists():
        return f"Ошибка: файл не найден ({file_path}). Для нового файла используй write_file."
    if not file_path.is_file():
        return f"Ошибка: {file_path} не является файлом."

    # 2. Stale check (10.9).
    from tools.git_ops import file_fingerprint

    fp = file_fingerprint(file_path)
    if expected_hash and not fp["hash"].startswith(expected_hash):
        return (
            f"Ошибка: stale check failed. Файл изменился с момента "
            f"чтения (expected hash {expected_hash[:8]}, "
            f"actual {fp['hash'][:8]}). Перечитай файл через "
            f"read_file и повтори apply_patch с новым expected_hash."
        )

    # 3. Читаем оригинал.
    try:
        original_content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Ошибка чтения файла: {e}"

    # 4. Парсим патч и применяем.
    try:
        new_content = _apply_unified_diff(original_content, patch)
    except _PatchError as e:
        return f"Ошибка применения патча: {e}"

    if new_content == original_content:
        return {
            "_llm_text": f"Файл не изменился: {file_path.name}",
            "status": "no_change",
            "path": str(file_path),
            "is_new_file": False,
            "original_content": original_content,
            "written_content": original_content,
            "diff": "",
            "lines_added": 0,
            "lines_removed": 0,
            "syntax_error": "",
            "lsp_errors": [],
            "snapshot_id": "",
        }

    # 5. Снапшот (10.9).
    snapshot_id = ""
    if project_root:
        try:
            from tools.git_ops import create_file_snapshot

            snapshot_id = create_file_snapshot(
                [file_path],
                tool="apply_patch",
                args_summary=f"{file_path.name} ({len(patch)} chars patch)",
                project_root=Path(project_root),
            )
        except Exception:
            logger.debug("apply_patch: снапшот не создан", exc_info=True)

    # 6. Запись.
    try:
        file_path.write_text(new_content, encoding="utf-8")
    except OSError as e:
        return f"Ошибка записи: {e}"
    logger.info(f"apply_patch: {file_path} ({len(original_content)} → {len(new_content)} chars)")

    # 7. Верификация (py_compile + LSP).
    syntax_error = _verify_python_syntax(file_path)
    lsp_errors: list[dict] = []
    try:
        from agents.lsp_bridge import LspBridge

        bridge = LspBridge._get_default()
        if bridge is not None:
            lsp_errors = bridge.diagnostics(file_path)
    except Exception:
        logger.debug("LSP diagnostics unavailable", exc_info=True)

    # 8. Diff и статистика.
    diff_chunks = list(
        difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{file_path.name}",
            tofile=f"b/{file_path.name}",
            n=3,
        )
    )
    diff_text = "".join(diff_chunks)
    lines_added = sum(1 for ln in diff_chunks if ln.startswith("+") and not ln.startswith("+++"))
    lines_removed = sum(1 for ln in diff_chunks if ln.startswith("-") and not ln.startswith("---"))

    llm_text = f"Файл успешно обновлён через apply_patch: {file_path.name}"
    if snapshot_id:
        llm_text += f". Снапшот: {snapshot_id}."
    if syntax_error:
        llm_text += f"\n\n⚠️ Синтаксическая ошибка Python (py_compile):\n{syntax_error}"
    if lsp_errors:
        preview = "; ".join(
            f"{e.get('file', '?')}:{e.get('line', '?')} {e.get('message', '')[:120]}" for e in lsp_errors[:5]
        )
        llm_text += f"\n\n⚠️ LSP: {len(lsp_errors)} ошибок: {preview}"

    return {
        "_llm_text": llm_text,
        "status": "ok",
        "path": str(file_path),
        "is_new_file": False,
        "original_content": original_content,
        "written_content": new_content,
        "diff": diff_text,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "syntax_error": syntax_error,
        "lsp_errors": lsp_errors,
        "snapshot_id": snapshot_id,
    }


class _PatchError(Exception):
    """Ошибка применения патча (контекст не совпал, битый формат)."""


def _apply_unified_diff(original: str, patch: str) -> str:
    """Применяет unified diff к тексту, возвращает результат.

    Поддерживает стандартный формат `git diff` / `diff -u`:
      * строки-заголовки ``---`` и ``+++`` игнорируются (файл уже открыт);
      * ``@@ -old_start,old_count +new_start,new_count @@`` — hunk;
      * `` `` (пробел) — контекст, должен совпасть;
      * ``-`` — удаляемая строка;
      * ``+`` — добавляемая строка.

    Ханки применяются последовательно, со сдвигом индекса. Контекст
    сверяется по точному совпадению — если не совпал, _PatchError.

    Args:
        original: Оригинальное содержимое.
        patch: Unified diff.

    Returns:
        Новое содержимое.

    Raises:
        _PatchError: Битый формат или несовпадение контекста.
    """
    orig_lines = original.splitlines(keepends=True)
    patch_lines = patch.splitlines(keepends=True)
    if not patch_lines:
        raise _PatchError("пустой patch")

    # Результат собираем через «сдвиг»: копим строки до очередного hunk'а
    # и после него — уже применённые. Индекс в orig_lines — курсор.
    result: list[str] = []
    cursor = 0  # позиция в orig_lines (сколько уже «прошло»)
    i = 0
    n = len(patch_lines)

    # Пропускаем возможные `---/+++` заголовки.
    while i < n and not patch_lines[i].startswith("@@"):
        line = patch_lines[i]
        if line.startswith(("---", "+++", "diff ", "index ")):
            i += 1
            continue
        # Если до первого hunk'а есть что-то ещё — это ошибка формата.
        if line.strip() == "":
            i += 1
            continue
        raise _PatchError(f"ожидался hunk @@ ... @@, получено {line!r}")

    while i < n:
        header = patch_lines[i].rstrip("\r\n")
        m = _HUNK_HEADER_RE.match(header)
        if not m:
            raise _PatchError(f"некорректный hunk-заголовок: {header!r}")
        old_start = int(m.group(1))
        i += 1

        # Собираем строки hunk'а до следующего `@@` или до конца.
        hunk_old: list[str] = []  # ожидаемые строки (context + removed)
        hunk_new: list[str] = []  # результат (context + added)
        while i < n:
            line = patch_lines[i]
            if line.startswith("@@"):
                break
            if line.startswith(("---", "+++", "diff ")):
                # Заголовок следующего файла — тоже стоп (но у нас один
                # файл, так что это скорее ошибка).
                break
            if line.startswith("\\"):
                # `\ No newline at end of file` — пропускаем.
                i += 1
                continue
            if not line:
                # Пустая строка в патче = контекстная пустая строка.
                hunk_old.append("\n")
                hunk_new.append("\n")
                i += 1
                continue
            marker = line[0]
            text = line[1:]
            if marker == " ":
                hunk_old.append(text)
                hunk_new.append(text)
            elif marker == "-":
                hunk_old.append(text)
            elif marker == "+":
                hunk_new.append(text)
            else:
                raise _PatchError(f"неизвестный префикс строки: {line[:40]!r}")
            i += 1

        # Ищем hunk_old в orig_lines начиная с (old_start - 1).
        # Стандарт: old_start 1-based. Если hunk пуст — просто вставка.
        target_idx = max(0, old_start - 1)

        # Допускаем неточное позиционирование: ищем блок в радиусе ±5.
        found_at = -1
        search_window = range(max(0, target_idx - 5), min(len(orig_lines), target_idx + 6))
        for start in search_window:
            if orig_lines[start : start + len(hunk_old)] == hunk_old:
                found_at = start
                break
        if found_at < 0:
            # Совсем не нашли — ищем по всему файлу (для патчей, где
            # old_start устарел).
            for start in range(len(orig_lines) - len(hunk_old) + 1):
                if orig_lines[start : start + len(hunk_old)] == hunk_old:
                    found_at = start
                    break
        if found_at < 0:
            # Диагностика: показываем ожидаемый и реальный контекст.
            expected = "".join(hunk_old[:3])
            actual_start = min(target_idx, len(orig_lines))
            actual = "".join(orig_lines[actual_start : actual_start + 3])
            raise _PatchError(
                f"контекст не совпал (hunk at old_start={old_start}).\n"
                f"Ожидалось:\n{expected}\n"
                f"Фактически:\n{actual}"
            )

        # Копируем всё, что было ДО найденного блока, из оригинала.
        while cursor < found_at:
            result.append(orig_lines[cursor])
            cursor += 1
        # Пропускаем hunk_old в оригинале, вставляем hunk_new.
        cursor += len(hunk_old)
        result.extend(hunk_new)

    # Остаток оригинала после последнего hunk'а.
    while cursor < len(orig_lines):
        result.append(orig_lines[cursor])
        cursor += 1
    return "".join(result)
