"""Кастомный делегат для подсветки Git-статуса в дереве файлов."""

import subprocess

from loguru import logger
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem
from typing import cast
from PySide6.QtWidgets import QWidget

_GIT_COLORS = {
    "M": QColor("#e06c75"),
    "A": QColor("#98c379"),
    "D": QColor("#be5046"),
    "R": QColor("#61afef"),
    "C": QColor("#56b6c2"),
    "U": QColor("#d19a66"),
    "??": QColor("#98c379"),
    "!!": QColor("#e06c75"),
}


class GitStatusDelegate(QStyledItemDelegate):
    """Делегат, окрашивающий имена файлов по Git-статусу.

    Цвета:
      - Зелёный  — новые (untracked) или добавленные (staged)
      - Красный  — изменённые (modified, unstaged)
      - Оранжевый — переименованные / скопированные
      - Серый    — без изменений (committed)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._repo_path: str | None = None
        self._status_map: dict[str, str] = {}
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh)

    def set_repo_path(self, path: str | None):
        """Устанавливает корень репозитория и запускает первую загрузку."""
        self._repo_path = path
        self._refresh()

    def refresh(self):
        """Запускает отложенное обновление статусов."""
        self._refresh_timer.start(500)

    def _refresh(self):
        """Парсит git status --porcelain для корня репозитория."""
        # self._status_map.clear()
        # if not self._repo_path:
        #     self.parent().update()
        #     return
        # try:
        #     result = subprocess.run(
        #         ["git", "status", "--porcelain"],
        #         capture_output=True, text=True, cwd=self._repo_path,
        #         timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
        #     )
        #     if result.returncode != 0:
        #         self.parent().update()
        #         return
        #     for line in result.stdout.splitlines():
        #         if len(line) < 3:
        #             continue
        #         raw_status = line[:2]
        #         rel_path = line[3:].strip()
        #         normalised = rel_path.replace("\\", "/")
        #         symbol = raw_status.strip() or raw_status[0]
        #         self._status_map[normalised] = symbol
        #     self.parent().update()
        # except FileNotFoundError:
        #     pass
        # except Exception as exc:
        #     logger.debug(f"Git status refresh error: {exc}")

        self._status_map.clear()

        # Приводим родителя к типу QWidget, у которого есть метод update()
        parent_widget = cast(QWidget, self.parent())

        if not self._repo_path:
            if parent_widget:
                parent_widget.update()
            return
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=self._repo_path,
                timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                if parent_widget:
                    parent_widget.update()
                return
            for line in result.stdout.splitlines():
                if len(line) < 3:
                    continue
                raw_status = line[:2]
                rel_path = line[3:].strip()
                normalised = rel_path.replace("\\", "/")
                symbol = raw_status.strip() or raw_status[0]
                self._status_map[normalised] = symbol

            if parent_widget:
                parent_widget.update()
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.debug(f"Git status refresh error: {exc}")




    def _status_for(self, file_path: str) -> str | None:
        """Возвращает Git-символ статуса для относительного пути."""
        if not self._status_map:
            return None
        symbol = self._status_map.get(file_path)
        if symbol:
            return symbol
        for rel_path, sym in self._status_map.items():
            if file_path.startswith(rel_path + "/"):
                return sym
        return None

    # def initStyleOption(self, option: QStyleOptionViewItem, index):
    #     """Настраивает опции отображения, устанавливая цвет текста по Git."""
    #     super().initStyleOption(option, index)

    #     if not self._repo_path or not self._status_map:
    #         return

    #     file_name = index.data(Qt.ItemDataRole.DisplayRole)
    #     if not file_name:
    #         return

    #     parts = []
    #     idx = index
    #     while idx.isValid():
    #         data = idx.data(Qt.ItemDataRole.DisplayRole)
    #         if data:
    #             parts.insert(0, data)
    #         idx = idx.parent()
    #     if len(parts) < 2:
    #         return
    #     rel_path = "/".join(parts[1:])

    #     symbol = self._status_for(rel_path)
    #     if not symbol:
    #         return

    #     color = _GIT_COLORS.get(symbol)
    #     if color:
    #         option.palette.setColor(option.palette.ColorRole.Text, color)

    def initStyleOption(self, option: QStyleOptionViewItem, index):
        """Настраивает опции отображения, устанавливая цвет текста по Git."""
        super().initStyleOption(option, index)

        if not self._repo_path or not self._status_map:
            return

        file_name = index.data(Qt.ItemDataRole.DisplayRole)
        if not file_name:
            return

        # 1. Исправляем [var-annotated]: явно указываем тип списка строк
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

        symbol = self._status_for(rel_path)
        if not symbol:
            return

        color = _GIT_COLORS.get(symbol)
        if color:
            # 2. Исправляем [attr-defined]: глушим ошибку генерации типов Qt для palette
            option.palette.setColor(option.palette.ColorRole.Text, color)  # type: ignore[attr-defined]
