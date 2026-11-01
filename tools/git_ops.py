"""Утилиты для работы с Git-снапшотами."""

import hashlib
import json
import shutil
import time
from datetime import datetime, timedelta
from core.utils import get_app_data_dir

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

# ---------------------------------------------------------------------------
# 10.9: снапшоты файлов перед мутирующими инструментами
# ---------------------------------------------------------------------------

_SNAPSHOTS_DIRNAME = "snapshots"
_META_FILENAME = "meta.json"


def _snapshots_root() -> Path:
    """Корень каталога снапшотов (`~/.ollidesk/snapshots/`)."""
    root = get_app_data_dir() / _SNAPSHOTS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_of_file(path: Path) -> str:
    """SHA-256 файла (для stale check)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4096), b""):
            h.update(block)
    return h.hexdigest()


def file_fingerprint(path: Path) -> dict[str, Any]:
    """Возвращает fingerprint файла: ``{"hash": ..., "mtime": ..., "size": ...}``.

    Используется для stale check перед правкой: `apply_patch` сверяет
    hash/mtime с тем, что было при чтении.

    Args:
        path: Файл.

    Returns:
        dict с ключами ``hash`` (sha256 hex), ``mtime`` (unix timestamp),
        ``size`` (bytes). Если файла нет — ``{"hash": "", "mtime": 0.0, "size": 0}``.
    """
    if not path.exists():
        return {"hash": "", "mtime": 0.0, "size": 0}
    try:
        st = path.stat()
        return {
            "hash": _sha256_of_file(path),
            "mtime": st.st_mtime,
            "size": st.st_size,
        }
    except OSError:
        return {"hash": "", "mtime": 0.0, "size": 0}


def create_file_snapshot(
    paths: list[Path],
    *,
    tool: str,
    args_summary: str = "",
    project_root: Path | None = None,
) -> str:
    """Создаёт снапшот указанных файлов, возвращает snapshot_id.

    Снапшот — копия файлов + ``meta.json`` (tool, args, created_at, root,
    files). Формат id: ``YYYYMMDD_HHMMSS_<8hex>`` — сортируемый по времени.

    Если файла ещё нет (новый файл), в метаданных фиксируется
    ``"existed": false``; откат для такого файла = удалить его.

    Args:
        paths: Файлы для снапшота (могут не существовать — тогда пишется
            запись «файла не было»).
        tool: Имя инструмента (``write_file``, ``apply_patch``, …).
        args_summary: Краткое описание аргументов (для метаданных).
        project_root: Корень проекта (для отката по относительным путям).

    Returns:
        snapshot_id.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nonce = hashlib.sha256(f"{timestamp}{tool}{args_summary}".encode()).hexdigest()[:8]
    snapshot_id = f"{timestamp}_{nonce}"
    snap_dir = _snapshots_root() / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    files_meta: list[dict[str, Any]] = []
    for src in paths:
        rel_name = src.name
        fingerprint = file_fingerprint(src)
        entry: dict[str, Any] = {
            "original_path": str(src),
            "name": rel_name,
            "existed": src.exists(),
            "hash": fingerprint["hash"],
            "mtime": fingerprint["mtime"],
        }
        if src.exists():
            try:
                shutil.copy2(src, snap_dir / rel_name)
                entry["saved_as"] = rel_name
            except OSError as e:
                entry["error"] = str(e)
        files_meta.append(entry)

    meta = {
        "snapshot_id": snapshot_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
        "args_summary": args_summary[:200],
        "project_root": str(project_root) if project_root else "",
        "files": files_meta,
    }
    (snap_dir / _META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"Snapshot {snapshot_id}: {len(paths)} файл(ов), tool={tool}")
    return snapshot_id


def load_snapshot_meta(snapshot_id: str) -> dict[str, Any] | None:
    """Читает ``meta.json`` снапшота. ``None``, если снапшот не найден."""
    path = _snapshots_root() / snapshot_id / _META_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Snapshot {snapshot_id}: meta.json не читается ({e})")
        return None


def restore_snapshot(snapshot_id: str) -> list[str]:
    """Восстанавливает файлы из снапшота.

    Args:
        snapshot_id: id, полученный от `create_file_snapshot`.

    Returns:
        Список восстановленных путей (могут быть относительными путями,
        если ``project_root`` был задан — тогда пути абсолютные).
        Новые файлы, которых не было при снапшоте, удаляются.

    Raises:
        FileNotFoundError: Если снапшот не найден.
    """
    meta = load_snapshot_meta(snapshot_id)
    if meta is None:
        raise FileNotFoundError(f"Снапшот не найден: {snapshot_id}")
    snap_dir = _snapshots_root() / snapshot_id
    restored: list[str] = []
    for entry in meta.get("files", []):
        original = Path(entry["original_path"])
        existed = bool(entry.get("existed"))
        saved_as = entry.get("saved_as")
        if existed and saved_as:
            src = snap_dir / saved_as
            if src.exists():
                try:
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, original)
                    restored.append(str(original))
                except OSError as e:
                    logger.warning(
                        f"Snapshot {snapshot_id}: восстановление {original} "
                        f"упало ({e})"
                    )
        elif not existed and original.exists():
            # Новый файл — удаляем при откате.
            try:
                original.unlink()
                restored.append(str(original))
            except OSError as e:
                logger.warning(
                    f"Snapshot {snapshot_id}: удаление {original} упало ({e})"
                )
    logger.info(f"Snapshot {snapshot_id}: восстановлено {len(restored)} файл(ов)")
    return restored


def cleanup_old_snapshots(*, retention_days: int = 7) -> int:
    """Удаляет снапшоты старше ``retention_days``.

    Args:
        retention_days: Срок хранения (``<= 0`` — без ограничения).

    Returns:
        Число удалённых снапшотов.
    """
    if retention_days <= 0:
        return 0
    root = _snapshots_root()
    if not root.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                shutil.rmtree(entry)
                removed += 1
            except OSError as e:
                logger.warning(f"cleanup_old_snapshots: {entry} не удалён ({e})")
    if removed:
        logger.info(f"Snapshots cleanup: удалено {removed} старше {retention_days} дн.")
    return removed