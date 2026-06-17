"""Инструменты для чтения и записи файлов."""

from pathlib import Path

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from tools.git_ops import create_snapshot


@tool(
    name="read_file",
    description="Читает содержимое файла по указанному пути",
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
)
def read_file(path: str, project_root: str = "") -> str:
    """Читает файл и возвращает его содержимое.

    Args:
        path: Путь к файлу (абсолютный или относительный)
        project_root: Корень проекта для проверки безопасности

    Returns:
        Содержимое файла
    """
    file_path = _resolve_path(path, project_root)
    if not file_path:
        return "Ошибка: путь вне допустимой директории"

    if not file_path.exists():
        return f"Файл не найден: {file_path}"

    if not file_path.is_file():
        return f"Это не файл: {file_path}"

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        logger.debug(f"Прочитан файл: {file_path} ({len(content)} символов)")
        return content
    except PermissionError:
        return f"Ошибка доступа: {file_path}"
    except Exception as e:
        return f"Ошибка чтения файла: {e}"


@tool(
    name="list_directory",
    description="Возвращает список файлов и папок в директории",
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
)
def list_directory(path: str, project_root: str = "") -> str:
    """Возвращает содержимое директории.

    Args:
        path: Путь к директории (абсолютный или относительный)
        project_root: Корень проекта для проверки безопасности

    Returns:
        Список файлов и папок
    """
    dir_path = _resolve_path(path, project_root)
    if not dir_path:
        return "Ошибка: путь вне допустимой директории"

    if not dir_path.exists():
        return f"Директория не найдена: {dir_path}"

    if not dir_path.is_dir():
        return f"Это не директория: {dir_path}"

    try:
        entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for entry in entries:
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{suffix}")
        result = "\n".join(lines)
        logger.debug(f"Прочитана директория: {dir_path} ({len(lines)} записей)")
        return result
    except PermissionError:
        return f"Ошибка доступа: {dir_path}"
    except Exception as e:
        return f"Ошибка чтения директории: {e}"


@tool(
    name="write_file",
    description="Записывает содержимое в файл. Перед записью создаёт Git-снапшот для отката.",
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
)
def write_file(path: str, content: str, project_root: str = "") -> str:
    """Записывает содержимое в файл.

    Перед записью создаёт Git-снапшот (stash) для возможности отката.

    Args:
        path: Путь к файлу (абсолютный или относительный)
        content: Содержимое для записи
        project_root: Корень проекта

    Returns:
        Сообщение о результате
    """
    file_path = _resolve_path(path, project_root)
    if not file_path:
        return "Ошибка: путь вне допустимой директории"

    if file_path.is_dir():
        return f"Ошибка: {file_path} является директорией"

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)

        snapshot_status = None
        if project_root:
            repo_path = Path(project_root)
            snapshot_status = create_snapshot(repo_path, message=f"Before agent write: {file_path.name}")

        file_path.write_text(content, encoding="utf-8")
        logger.info(f"Файл записан: {file_path} ({len(content)} символов)")

        if snapshot_status:
            return f"Файл успешно обновлён: {file_path.name}. Создан Git-снапшот для отката."
        else:
            return f"Файл успешно обновлён: {file_path.name}. (Git-снапшот не создан – проверьте наличие репозитория или наличие изменений.)"

    except PermissionError:
        return f"Ошибка доступа при записи: {file_path}"
    except Exception as e:
        return f"Ошибка записи файла: {e}"


def _resolve_path(path: str, project_root: str) -> Path | None:
    """Безопасно резолвит путь, не давая выйти за пределы project_root.

    Args:
        path: Запрашиваемый путь
        project_root: Корень проекта

    Returns:
        Path или None, если путь вне допустимой зоны
    """
    p = Path(path)
    if p.is_absolute():
        resolved = p.resolve()
    elif project_root:
        resolved = (Path(project_root) / p).resolve()
    else:
        resolved = p.resolve()

    if project_root:
        root = Path(project_root).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return None

    return resolved
