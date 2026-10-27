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


def read_file_at_head(repo_path: Path | str, file_path: Path | str) -> str | None:
    """[9.8] Читает содержимое файла из HEAD (последний коммит).

    Используется для diff-режима редактора: HEAD-версия ↔ рабочая копия.
    Не выносит subprocess в UI — вся git-логика живёт здесь.

    Args:
        repo_path: Путь к корню репозитория (или любой каталог внутри).
        file_path: Абсолютный путь к файлу ИЛИ относительный от repo_path.

    Returns:
        Содержимое файла в HEAD или ``None``, если:
          * репозиторий не найден;
          * файл не отслеживается git;
          * git недоступен (не установлен / таймаут);
          * файл добавлен, но ещё ни разу не коммитился.
    """
    try:
        repo = Repo(Path(repo_path) if isinstance(repo_path, str) else repo_path)
    except Exception:
        return None

    target = Path(file_path)
    try:
        if target.is_absolute():
            rel = target.resolve().relative_to(Path(repo.working_dir).resolve()).as_posix()
        else:
            rel = target.as_posix()
    except ValueError:
        return None

    try:
        content: str = repo.git.show(f"HEAD:{rel}")
        return content
    except Exception:
        # Файл не в HEAD (новый), git-ошибка, таймаут — тихий None.
        return None


def get_git_status_for_file(repo_path: Path | str, file_path: Path | str) -> str | None:
    """[9.8] Возвращает двухбуквенный git-статус файла (XY из porcelain).

    Args:
        repo_path: Путь к корню репозитория.
        file_path: Абсолютный путь к файлу ИЛИ относительный от repo_path.

    Returns:
        ``"M "``, ``" M"``, ``"??"``, ``"A "``, … или ``None``, если файл
        не изменён / не в репозитории / git недоступен.
    """
    try:
        repo = Repo(Path(repo_path) if isinstance(repo_path, str) else repo_path)
    except Exception:
        return None

    target = Path(file_path)
    try:
        if target.is_absolute():
            rel = target.resolve().relative_to(Path(repo.working_dir).resolve()).as_posix()
        else:
            rel = target.as_posix()
    except ValueError:
        return None

    try:
        # `--porcelain` возвращает строки "XY path". Ищем наш файл.
        out: str = repo.git.status("--porcelain", "--", rel)
    except Exception:
        return None
    if not out:
        return None
    line = out.splitlines()[0]
    if len(line) < 3:
        return None
    return line[:2]
