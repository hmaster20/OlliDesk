"""QCompleter для `@`-команд (9.6.2).

Показывает popup со списком команд при наборе `@` в начале строки.
Фильтрация — по подстроке (без учёта регистра); активация эмитит
`command_activated(tag)`, ChatPanel вставляет `tag + " "`.

`setWidget(input_edit)` НЕ используется: стандартный QCompleter
срабатывает по Ctrl+Space, а нам нужно — на каждый `@`-ввод. Поэтому
ChatPanel вызывает `show_for_prefix(prefix)` вручную из `textChanged`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QCompleter, QWidget

from core.chat_commands import ChatCommand, match_commands


class CommandCompleter(QCompleter):
    """QCompleter с моделью «tag — hint» для `@`-команд."""

    command_activated = Signal(str)  # tag

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = QStandardItemModel(self)
        self._model.setColumnCount(2)
        self._model.setHorizontalHeaderLabels(["Команда", "Описание"])
        self.setModel(self._model)
        self.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self.setWrapAround(False)
        # Показываем обе колонки (tag + hint).
        self.setCompletionColumn(0)
        # Ширина popup.
        self.popup().setMinimumWidth(360)
        self.popup().setStyleSheet(
            "QListView { background: #2d2d2d; color: #d4d4d4;"
            " border: 1px solid #444; padding: 2px; font-size: 12px; }"
            " QListView::item { padding: 4px 6px; }"
            " QListView::item:selected { background: #4a6741; color: #fff; }"
        )

        # Активация: клик/Enter в popup.
        self.activated.connect(self._on_activated)

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def show_for_prefix(self, prefix: str) -> None:
        """Фильтрует и показывает popup для заданного префикса.

        Если совпадений нет — popup скрывается. Регистр при сравнении
        игнорируется; `tag` в модели — уже с `@`.
        """
        matches = match_commands(prefix)
        self._rebuild_model(matches)
        if not matches:
            self.popup().hide()
            return
        self.setCompletionPrefix(prefix)
        if self.popup().isVisible():
            self.popup().setCurrentIndex(self.completionModel().index(0, 0))
        else:
            self.complete()

    def hide_popup(self) -> None:
        """Скрывает popup (например, при потере `@`-контекста)."""
        if self.popup().isVisible():
            self.popup().hide()

    def is_popup_visible(self) -> bool:
        return self.popup().isVisible()

    # ------------------------------------------------------------------
    # Внутренние
    # ------------------------------------------------------------------

    def _rebuild_model(self, commands: list[ChatCommand]) -> None:
        self._model.clear()
        self._model.setHorizontalHeaderLabels(["Команда", "Описание"])
        for c in commands:
            tag_item = QStandardItem(c.tag)
            tag_item.setData(c.placeholder, Qt.ItemDataRole.UserRole)
            hint_item = QStandardItem(c.hint)
            hint_item.setForeground(Qt.GlobalColor.gray)
            self._model.appendRow([tag_item, hint_item])

    def _on_activated(self, tag: str) -> None:
        if tag:
            self.command_activated.emit(tag)
