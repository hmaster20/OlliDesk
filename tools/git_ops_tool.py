"""Инструменты для работы с Git, доступные агенту."""

from typing import Any

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from tools.git_ops import create_snapshot, get_git_status


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


@tool(
    name="git_commit",
    description=(
        "Создаёт коммит в текущей ветке. Используй ТОЛЬКО после того, как "
        "пользователь попросил закоммитить изменения или сам предложил это "
        "в задаче. НЕ коммить без явного запроса.\n\n"
        "Аргументы:\n"
        "  • message — сообщение в стиле Conventional Commits "
        "(«fix: …», «feat: …», «refactor: …»).\n"
        "  • files — список путей для коммита (относительно корня "
        "проекта). Если не задан — закоммитятся только уже отслеживаемые "
        "изменения (`git commit -a`), untracked-файлы НЕ добавятся."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
    is_mutating=True,
    category="git",
    capabilities=["git"],
    timeout_s=60.0,
)
def tool_git_commit(
    message: str,
    files: list[str] | None = None,
    project_root: str = "",
) -> dict[str, Any] | str:
    """Создаёт git-коммит.

    Args:
        message: Сообщение коммита (Conventional Commits).
        files: Список файлов (относительно project_root) или None.
        project_root: Корень проекта.

    Returns:
        dict с ``_llm_text``, ``commit_hash``, ``branch``, ``files_committed``
        для UI-карточки, или строка с ошибкой.
    """
    if not project_root:
        return "Ошибка: git_commit требует открытый проект."
    if not message or not message.strip():
        return "Ошибка: пустое сообщение коммита."

    from core.sandbox import SandboxedPathResolver

    # Валидация путей через песочницу.
    normalized_files: list[str] | None = None
    if files:
        resolver = SandboxedPathResolver(project_root)
        normalized_files = []
        for f in files:
            resolved = resolver.resolve(f, allow_sensitive=False)
            if resolved is None:
                return f"Ошибка: путь '{f}' вне проекта."
            try:
                rel = resolved.relative_to(resolver.root).as_posix()
            except (ValueError, AttributeError):
                return f"Ошибка: путь '{f}' вне проекта."
            normalized_files.append(rel)

    from tools.git_ops import commit as _commit

    result = _commit(project_root, message, normalized_files)
    if not result.get("ok"):
        return f"Ошибка коммита: {result.get('error', '?')}"

    llm_text = (
        f"✅ Коммит создан: {result['commit_hash']} "
        f"в ветке {result['branch']}.\n"
        f"Сообщение: {message.strip()[:120]}\n"
        f"Файлов закоммичено: {len(result['files_committed'])}"
    )
    if result["files_committed"]:
        llm_text += "\n" + "\n".join(f"  • {f}" for f in result["files_committed"][:20])

    # [9.4] Событие в EventBus для UI-ленты.
    try:
        from core.event_bus import emit_event
        from core.events import EventKind

        emit_event(
            EventKind.GIT_COMMIT_CREATED.value,
            {
                "branch": result["branch"],
                "commit_hash": result["commit_hash"],
                "files": result["files_committed"],
                "message": message.strip()[:200],
            },
        )
    except Exception:
        pass

    return {
        "_llm_text": llm_text,
        "commit_hash": result["commit_hash"],
        "branch": result["branch"],
        "files_committed": result["files_committed"],
    }
