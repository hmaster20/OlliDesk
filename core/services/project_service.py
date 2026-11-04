"""Сервис проектного контекста (2.8).

`ProjectService` — единая точка для:
  * **кэш `ProjectIntel`** (сейчас дублируется: `Orchestrator._intel_memo`
    и повторные вызовы `analyze_project` из `MainWindow`/`ChatPanel`);
  * **git-снимок** (ветка/hash/timestamp) — для `ProjectSettings`;
  * **git_context для промпта** — вынести `ChatPanel._build_git_context`
    из виджета.

TTL кэша — 5 минут. При `force=True` пересчитывает.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from core.config import AgentMode
from fs.intel import ProjectIntel, analyze_project

DEFAULT_INTEL_TTL_S = 300.0


@dataclass(frozen=True)
class GitSnapshot:
    """Снимок git-состояния проекта."""

    branch: str
    commit_hash: str
    timestamp: str
    recent_commits: list[dict[str, str]]
    uncommitted_diff: str


class ProjectService:
    """Кэш `ProjectIntel` + git-снимки.

    Потокобезопасность: `threading.RLock` вокруг кэша `_intel_cache`.
    """

    def __init__(self, *, intel_ttl_s: float = DEFAULT_INTEL_TTL_S) -> None:
        self._intel_cache: dict[str, tuple[float, ProjectIntel]] = {}
        self._intel_ttl_s = intel_ttl_s
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # ProjectIntel
    # ------------------------------------------------------------------

    def get_intel(
        self,
        root: Path | str,
        *,
        cache_dir: Path | None = None,
        force: bool = False,
    ) -> ProjectIntel:
        """Возвращает `ProjectIntel` для корня (кэш по TTL).

        Args:
            root: Корень проекта.
            cache_dir: Каталог кэша graph/evidence (``.ollidesk/graph``).
            force: ``True`` — пересчитать принудительно.

        Returns:
            `ProjectIntel`.
        """
        key = str(Path(root).resolve())
        now = time.time()
        with self._lock:
            if not force:
                cached = self._intel_cache.get(key)
                if cached is not None:
                    ts, intel = cached
                    if now - ts < self._intel_ttl_s:
                        logger.debug(
                            f"ProjectService: intel cache hit {key} "
                            f"({now - ts:.0f}s)"
                        )
                        return intel

        intel = analyze_project(Path(key), cache_dir=cache_dir)
        with self._lock:
            self._intel_cache[key] = (now, intel)
        logger.info(
            f"ProjectService: intel {key} — {intel.file_count} файлов, "
            f"{len(intel.entry_points)} entry, {len(intel.graph.nodes)} узлов"
        )
        return intel

    def invalidate(self, root: Path | str) -> None:
        """Сбрасывает кэш для корня."""
        key = str(Path(root).resolve())
        with self._lock:
            self._intel_cache.pop(key, None)

    def clear(self) -> None:
        """Сбрасывает весь кэш."""
        with self._lock:
            self._intel_cache.clear()

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------

    def git_snapshot(self, root: Path | str) -> GitSnapshot:
        """Собирает снимок git-состояния.

        Возвращает пустые поля, если проект не под git.
        """
        from tools.git_ops import (
            get_branch_hash,
            get_current_branch,
            get_recent_commits,
            get_uncommitted_diff,
        )

        p = Path(root)
        if not (p / ".git").exists():
            return GitSnapshot("", "", "", [], "")
        from datetime import datetime

        return GitSnapshot(
            branch=get_current_branch(p) or "",
            commit_hash=get_branch_hash(p) or "",
            timestamp=datetime.now().isoformat(timespec="seconds"),
            recent_commits=get_recent_commits(p, limit=5),
            uncommitted_diff=get_uncommitted_diff(p, max_chars=1500),
        )

    def build_git_context(
        self,
        root: Path | str,
        *,
        mode: AgentMode,
    ) -> str:
        """Собирает блок для системного промпта (только в AGENT).

        Формат:
            Ветка: feature/x (abc1234)
            Последние коммиты:
            • a1b2c3d 2025-01-01 feat: added parser
            Незакоммиченные изменения (diff):
            …
        В CHAT/PLAN — пустая строка (экономит токены).
        """
        if mode != AgentMode.AGENT:
            return ""
        try:
            snap = self.git_snapshot(root)
            if not snap.branch:
                return ""
            lines: list[str] = [
                f"Ветка: {snap.branch} ({snap.commit_hash})"
            ]
            if snap.recent_commits:
                lines.append("Последние коммиты:")
                for c in snap.recent_commits:
                    lines.append(
                        f"  • {c['hash']} {c['date']} "
                        f"{c['subject'][:80]}"
                    )
            if snap.uncommitted_diff:
                lines.append("Незакоммиченные изменения (diff):")
                lines.append(snap.uncommitted_diff)
            return "\n".join(lines)
        except Exception:
            logger.debug(
                "ProjectService.build_git_context упал", exc_info=True
            )
            return ""

__all__ = ["GitSnapshot", "ProjectService", "DEFAULT_INTEL_TTL_S"]