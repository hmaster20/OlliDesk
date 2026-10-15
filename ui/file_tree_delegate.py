"""Кастомный делегат для подсветки Git-статуса в дереве файлов."""

from __future__ import annotations

import subprocess
from typing import cast

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTreeView,
)

# Цвета по одному символу XY из `git status --porcelain`.
# X — индекс, Y — рабочее дерево.
_GIT_COLORS: dict[str, QColor] = {
    "M": QColor("#e5c07b"),   # modified — жёлтый
    "A": QColor("#98c379"),   # added — зелёный
    "D": QColor("#e06c75"),   # deleted — красный
    "R": QColor("#61afef"),   # renamed — синий
    "C": QColor("#56b6c2"),   # copied — бирюзовый
    "U": QColor("#d19a66"),   # unmerged — оранжевый
    "?": QColor("#98c379"),   # untracked — зелёный
    "!": QColor("#5c6370"),   # ignored — серый
}


class GitStatusDelegate(QStyledItemDelegate):
    """Делегат, окрашивающий имена файлов по Git-статусу.

    Цвета:
      - Зелёный  — новые (untracked) или добавленные (staged)
      - Красный  — изменённые (modified, unstaged)
      - Оранжевый — переименованные / скопированные
      - Серый    — без изменений (committed)
    """

    branch_changed = Signal(str)

    def __init__(self, parent: QTreeView | None = None) -> None:
        super().__init__(parent)
        self._repo_path: str | None = None
        self._status_map: dict[str, str] = {}
        self._branch: str = ""

        # Дебаунс для ручного refresh().
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._do_refresh)

        # Периодический опрос, пока виджет жив.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5000)
        self._poll_timer.timeout.connect(self._do_refresh)

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def set_repo_path(self, path: str | None) -> None:
        self._repo_path = path
        if path:
            self._poll_timer.start()
        else:
            self._poll_timer.stop()
            self._status_map.clear()
            self._branch = ""
            self.branch_changed.emit("")
        self.refresh()

    def refresh(self) -> None:
        """Отложенное обновление (debounce 300 мс)."""
        self._refresh_timer.start(300)

    @property
    def current_branch(self) -> str:
        return self._branch

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------

    def _run_git(self, *args: str, timeout: float = 5.0) -> str | None:
        if not self._repo_path:
            return None
        try:
            result = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self._repo_path,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.debug(f"git {' '.join(args)} failed: {e}")
            return None
        if result.returncode != 0:
            return None
        return result.stdout

    def _read_branch(self) -> str:
        out = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if out:
            branch = out.strip()
            if branch and branch != "HEAD":
                return branch
        # Detached HEAD
        out = self._run_git("rev-parse", "--short", "HEAD")
        return f"({out.strip()})" if out else ""

    def _do_refresh(self) -> None:
        self._status_map.clear()

        parent = cast(QTreeView, self.parent())

        if not self._repo_path:
            if parent:
                parent.viewport().update()
            return

        # Ветка
        branch = self._read_branch()
        if branch != self._branch:
            self._branch = branch
            self.branch_changed.emit(branch)

        # Статусы: обычный porcelain (без -z), --untracked-files=all даёт
        # перечисление каждого untracked-файла, а не только каталога.
        out = self._run_git(
            "status", "--porcelain", "--untracked-files=all"
        )
        if out is None:
            if parent:
                parent.viewport().update()
            return

        for line in out.splitlines():
            if len(line) < 4:
                continue
            xy = line[:2]
            path = line[3:]

            # Rename / copy: "R  old -> new" — берём новый путь.
            if " -> " in path:
                path = path.split(" -> ", 1)[1]

            # Кавычки означают экранирование спецсимволов.
            if path.startswith('"') and path.endswith('"'):
                try:
                    path = path[1:-1].encode().decode("unicode_escape")
                except Exception:
                    path = path[1:-1]

            self._status_map[path.replace("\\", "/")] = xy

        if parent:
            parent.viewport().update()

    # ------------------------------------------------------------------
    # QStyledItemDelegate
    # ------------------------------------------------------------------

    def _status_for(self, file_path: str) -> str | None:
        if not self._status_map:
            return None
        xy = self._status_map.get(file_path)
        if xy is not None:
            return xy
        # Для каталогов — берём статус любого вложенного файла.
        prefix = file_path + "/"
        for rel_path, sym in self._status_map.items():
            if rel_path.startswith(prefix):
                return sym
        return None

    def initStyleOption(  # noqa: N802 (Qt override)
        self, option: QStyleOptionViewItem, index
    ) -> None:
        super().initStyleOption(option, index)

        if not self._repo_path or not self._status_map:
            return

        parts: list[str] = []
        idx = index
        while idx.isValid():
            data = idx.data(Qt.ItemDataRole.DisplayRole)
            if data:
                parts.insert(0, data)
            idx = idx.parent()
        if len(parts) < 2:
            return
        rel_path = "/".join(parts[1:])

        xy = self._status_for(rel_path)
        if not xy:
            return

        # Приоритет: worktree (Y) > index (X).
        x, y = xy[0], xy[1]
        if xy == "??":
            color = _GIT_COLORS["?"]
        elif xy == "!!":
            color = _GIT_COLORS["!"]
        elif y != " " and y in _GIT_COLORS:
            color = _GIT_COLORS[y]
        elif x != " " and x in _GIT_COLORS:
            color = _GIT_COLORS[x]
        else:
            return

        option.palette.setColor(QPalette.ColorRole.Text, color)
        option.palette.setColor(QPalette.ColorRole.HighlightedText, color)

        if xy == "!!":
            font = QFont(option.font)
            font.setItalic(True)
            option.font = font