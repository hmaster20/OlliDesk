"""Инструменты для работы с Git, доступные агенту."""

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from tools.git_ops import create_snapshot, get_git_status, undo_last_snapshot


@tool(
    name="get_git_status",
    description="Показывает текущий статус Git-репозитория: ветка, изменённые файлы",
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
    capabilities=["git"],
    timeout_s=15.0,
)
def tool_git_status(project_root: str = "") -> str:
    """Возвращает статус Git-репозитория.

    Args:
        project_root: Путь к корню проекта

    Returns:
        Статус репозитория
    """
    if not project_root:
        return "project_root не указан"
    return get_git_status(project_root)


@tool(
    name="create_snapshot",
    description="Создаёт Git-снапшот (stash) текущего состояния проекта",
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
    is_mutating=True,
    capabilities=["git"],
    timeout_s=15.0,
)
def tool_create_snapshot(project_root: str = "") -> str:
    """Создаёт снапшот текущего состояния проекта.

    Args:
        project_root: Путь к корню проекта

    Returns:
        Результат операции
    """
    if not project_root:
        return "project_root не указан"
    result = create_snapshot(project_root, message="Agent snapshot")
    if result:
        return f"Снапшот создан: {result}"
    return "Нет изменений для снапшота или ошибка"


@tool(
    name="undo_file_snapshot",
    description=(
        "Восстанавливает файлы из файлового снапшота (10.9). "
        "Принимает snapshot_id из metadata write_file/apply_patch. "
        "Новые файлы удаляются, существующие возвращаются к предыдущей версии."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
    is_mutating=True,
    category="git",
    capabilities=["git", "fs.write"],
    timeout_s=15.0,
)
def tool_undo_file_snapshot(snapshot_id: str = "") -> str:
    """Восстанавливает файлы из файлового снапшота."""
    if not snapshot_id:
        return "Ошибка: не указан snapshot_id"
    try:
        from tools.git_ops import restore_snapshot

        restored = restore_snapshot(snapshot_id)
    except FileNotFoundError:
        return f"Ошибка: снапшот {snapshot_id} не найден"
    except Exception as e:
        logger.exception("undo_file_snapshot failed")
        return f"Ошибка восстановления: {e}"
    if not restored:
        return f"Снапшот {snapshot_id}: нечего восстанавливать"
    return f"Восстановлено {len(restored)} файл(ов):\n" + "\n".join(restored)