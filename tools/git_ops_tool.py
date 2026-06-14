"""Инструменты для работы с Git, доступные агенту."""


from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from tools.git_ops import create_snapshot, get_git_status, undo_last_snapshot


@tool(
    name="get_git_status",
    description="Показывает текущий статус Git-репозитория: ветка, изменённые файлы",
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.PLAN, AgentMode.AGENT],
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
    name="undo_last_snapshot",
    description="Откатывает последний Git-снапшот (stash pop), восстанавливая предыдущее состояние",
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
)
def tool_undo_snapshot(project_root: str = "") -> str:
    """Откатывает последний снапшот.

    Args:
        project_root: Путь к корню проекта

    Returns:
        Результат операции
    """
    if not project_root:
        return "project_root не указан"
    result = undo_last_snapshot(project_root)
    if result:
        return f"Снапшот откатан:\n{result}"
    return "Нет снапшота для отката"
