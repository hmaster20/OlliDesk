"""Утилиты для работы с Git-снапшотами."""

from pathlib import Path

from git import Repo
from loguru import logger


def create_snapshot(repo_path: Path | str, message: str = "OlliDesk auto-snapshot") -> str | None:
    """Создаёт Git-снапшот (stash) перед изменением файлов.

    Args:
        repo_path: Путь к Git-репозиторию
        message: Сообщение для снапшота

    Returns:
        Ref имени снапшота или None, если репозиторий не найден
    """
    try:
        repo = Repo(Path(repo_path) if isinstance(repo_path, str) else repo_path)
        if repo.bare or repo.is_dirty(untracked_files=True) is False:
            return None

        stash_ref: str | None = repo.git.stash("push", "--include-untracked", "-m", message)
        logger.info(f"Создан Git-снапшот: {message}")
        return stash_ref
    except Exception as e:
        logger.warning(f"Не удалось создать Git-снапшот: {e}")
        return None


def undo_last_snapshot(repo_path: Path | str) -> str | None:
    """Откатывает последний снапшот (stash pop).

    Args:
        repo_path: Путь к Git-репозиторию

    Returns:
        Результат операции или None
    """
    try:
        repo = Repo(Path(repo_path) if isinstance(repo_path, str) else repo_path)
        result: str | None = repo.git.stash("pop")
        logger.info("Снапшот откатан (stash pop)")
        return result
    except Exception as e:
        logger.warning(f"Не удалось откатить снапшот: {e}")
        return None


def get_git_status(repo_path: Path | str) -> str:
    """Возвращает короткий статус Git-репозитория.

    Args:
        repo_path: Путь к Git-репозиторию

    Returns:
        Строка со статусом
    """
    try:
        repo = Repo(Path(repo_path) if isinstance(repo_path, str) else repo_path)
        if repo.bare:
            return "Репозиторий bare"

        lines = []
        if repo.is_dirty():
            lines.append("📝 Есть незакоммиченные изменения:")
            status = repo.git.status("--short")
            lines.append(status)
        else:
            lines.append("✅ Чистое рабочее дерево")

        branch = repo.active_branch.name
        lines.insert(0, f"🌿 Ветка: {branch}")

        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка получения статуса Git: {e}"
