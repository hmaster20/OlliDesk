"""Кастомный делегат для подсветки Git-статуса в дереве файлов.

Изменения относительно предыдущей версии:
* Починен критический баг: `parent.viewport` — это метод, а не свойство.
  Раньше `_do_refresh` падал с AttributeError и ломал paint-цикл дерева.
* Ветка теперь умеет показывать тег: если HEAD стоит ровно на теге —
  показывается имя тега, а не короткий хеш.
* Рядом с именем файла рисуется буква статуса (M/A/D/R/C/U/I), как в VS Code.
"""

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

# Отображаемая буква для каждого XY-статуса (для показа рядом с именем).
_GIT_LETTERS: dict[str, str] = {
    "M": "M",
    "A": "A",
    "D": "D",
    "R": "R",
    "C": "C",
    "U": "U",
    "?": "U",  # untracked
    "!": "I",  # ignored
}


class GitStatusDelegate(QStyledItemDelegate):
    """Делегат, окрашивающий имена файлов по Git-статусу."""

    branch_changed = Signal(str)

    def __init__(self, parent: QTreeView | None = None) -> None:
        super().__init__(parent)
        # Храним прямую ссылку на виджет — не полагаемся на self.parent(),
        # потому что в связке через QTreeView parent может быть None,
        # а getattr по имени легко вернуть метод вместо атрибута.
        self._view: QTreeView | None = parent
        self._repo_path: str | None = None
        self._status_map: dict[str, str] = {}
        self._branch: str = ""

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._do_refresh)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5000)
        self._poll_timer.timeout.connect(self._do_refresh)

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def set_view(self, view: QTreeView) -> None:
        """Позволяет переопределить виджет-владелец (после reload дерева)."""
        self._view = view

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
        """Текущая ветка / тег / short-hash.

        Приоритет:
          1. Если HEAD стоит ровно на теге — показываем тег (например, `v0.1.2`).
          2. Если мы на обычной ветке — её имя.
          3. Иначе (detached HEAD без точного тега) — `describe --tags` (ближайший
             тег + hash), либо короткий hash.
        """
        tag = self._run_git("describe", "--tags", "--exact-match", "HEAD")
        if tag and tag.strip():
            return tag.strip()

        ref = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if ref:
            branch = ref.strip()
            if branch and branch != "HEAD":
                return branch

        described = self._run_git("describe", "--tags", "--always", "--abbrev=7")
        if described and described.strip():
            return described.strip()

        short = self._run_git("rev-parse", "--short", "HEAD")
        return f"({short.strip()})" if short else ""

    def _do_refresh(self) -> None:
        """Точка входа обоих таймеров. Любая ошибка — в лог, не наружу."""
        try:
            self._status_map.clear()

            if not self._repo_path:
                self._update_viewport()
                return

            # --- Ветка ---
            try:
                branch = self._read_branch()
            except Exception:
                logger.debug("GitStatusDelegate: чтение ветки упало", exc_info=True)
                branch = ""

            if branch != self._branch:
                self._branch = branch
                try:
                    self.branch_changed.emit(branch)
                except Exception:
                    logger.debug("GitStatusDelegate: branch_changed handler упал", exc_info=True)

            # --- Статусы ---
            out = self._run_git("status", "--porcelain", "--untracked-files=all")
            if out is not None:
                for raw_line in out.splitlines():
                    try:
                        self._parse_porcelain_line(raw_line)
                    except Exception:
                        logger.debug(f"GitStatusDelegate: не разобрал строку: {raw_line!r}")

            self._update_viewport()
        except Exception:
            logger.exception("GitStatusDelegate._do_refresh failed")

    def _update_viewport(self) -> None:
        """Безопасно перерисовывает viewport дерева.

        ВАЖНО: `viewport` у QAbstractItemView — это **метод**, а не атрибут.
        Раньше вызов `getattr(parent, "viewport", None)` возвращал bound method,
        и `viewport.update()` падал с AttributeError.
        """
        view = self._view
        if view is None:
            return
        try:
            view.viewport().update()
        except Exception:
            logger.debug("GitStatusDelegate: не удалось обновить viewport", exc_info=True)

    def _parse_porcelain_line(self, line: str) -> None:
        """Разбирает одну строку `git status --porcelain`."""
        if len(line) < 4:
            return
        xy = line[:2]
        path = line[3:]

        # Rename / copy: "R  old -> new" — берём новый путь.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]

        # Экранированные пути вида `"caf\303\251.txt"`.
        if path.startswith('"') and path.endswith('"'):
            inner = path[1:-1]
            try:
                path = inner.encode("latin-1", "backslashreplace").decode("unicode_escape")
            except Exception:
                path = inner

        self._status_map[path.replace("\\", "/")] = xy

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
        try:
            super().initStyleOption(option, index)
        except Exception:
            return

        try:
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

            x, y = xy[0], xy[1]
            letter: str | None = None
            color: QColor | None = None

            if xy == "??":
                letter = _GIT_LETTERS["?"]
                color = _GIT_COLORS["?"]
            elif xy == "!!":
                letter = _GIT_LETTERS["!"]
                color = _GIT_COLORS["!"]
            elif y != " " and y in _GIT_COLORS:
                letter = _GIT_LETTERS.get(y, y)
                color = _GIT_COLORS[y]
            elif x != " " and x in _GIT_COLORS:
                letter = _GIT_LETTERS.get(x, x)
                color = _GIT_COLORS[x]
            else:
                return

            option.palette.setColor(QPalette.ColorRole.Text, color)
            option.palette.setColor(QPalette.ColorRole.HighlightedText, color)

            # Добавляем букву рядом с именем, как в VS Code.
            if letter and option.text:
                option.text = f"{option.text}  {letter}"
        except Exception:
            logger.debug("GitStatusDelegate.initStyleOption failed", exc_info=True)