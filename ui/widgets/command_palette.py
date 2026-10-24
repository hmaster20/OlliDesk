"""QDialog-палитра команд (9.9).

Открывается по Ctrl+Shift+P (глобальный shortcut MainWindow) или из
`View → Палитра команд…`. Набор команд — из `CommandRegistry`
(заполняется в MainWindow через `_register_palette_commands`).

Поведение:
  * `QLineEdit` — фокус при показе, фильтр «на лету»;
  * `QListWidget` — 2 строки на элемент: label (жирным) + hint/group/shortcut;
  * Enter / клик — выполнить выбранную команду и закрыть;
  * Esc — закрыть без выполнения;
  * ↑/↓ — навигация (обрабатывается QListWidget по умолчанию).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.command_registry import CommandRegistry, PaletteCommand


class CommandPalette(QDialog):
    """Модальный диалог-палитра команд."""

    def __init__(
        self,
        registry: CommandRegistry,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._visible_commands: list[PaletteCommand] = []

        self.setWindowTitle("Палитра команд")
        self.setWindowFlag(Qt.WindowType.Popup, False)
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setMinimumHeight(420)

        self._build_ui()
        self._refresh("")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Введите команду: «открыть», «профиль», «бэкенд», «дерево», «slm»…")
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._execute_current)
        font = QFont()
        font.setPointSize(12)
        self.search.setFont(font)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.setObjectName("command_palette_list")
        self.list.setStyleSheet(
            "QListWidget { background: #2d2d2d; color: #d4d4d4;"
            " border: 1px solid #444; border-radius: 4px; padding: 2px; }"
            "QListWidget::item { padding: 6px 10px; border-radius: 3px; }"
            "QListWidget::item:selected { background: #4a6741; color: #fff; }"
        )
        self.list.itemActivated.connect(self._execute_current)
        self.list.itemClicked.connect(self._execute_current)
        layout.addWidget(self.list, stretch=1)

        # Перехватываем Esc и Enter до QDialog (для консистентности).
        self.search.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == event.Type.KeyPress:
            key_event = event  # QKeyEvent
            if isinstance(key_event, QKeyEvent):
                if key_event.key() == Qt.Key.Key_Escape:
                    self.reject()
                    return True
                if key_event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                    # Передаём в list для навигации.
                    self.list.setFocus()
                    self.list.keyPressEvent(key_event)
                    self.search.setFocus()
                    return True
                if key_event.key() == Qt.Key.Key_PageDown:
                    self.list.setFocus()
                    self.list.keyPressEvent(key_event)
                    self.search.setFocus()
                    return True
                if key_event.key() == Qt.Key.Key_PageUp:
                    self.list.setFocus()
                    self.list.keyPressEvent(key_event)
                    self.search.setFocus()
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Фильтрация и выполнение
    # ------------------------------------------------------------------

    @Slot(str)
    def _refresh(self, text: str) -> None:
        self._visible_commands = self._registry.filter(text)
        self.list.clear()

        for cmd in self._visible_commands:
            item = QListWidgetItem(self._format_item(cmd))
            item.setData(Qt.ItemDataRole.UserRole, cmd.id)
            # Вторая строка — hint/shortcut/group (используем тот же text,
            # т.к. QListWidget не поддерживает многострочность без delegate).
            item.setToolTip((cmd.hint or cmd.group) + (f"  ({cmd.shortcut})" if cmd.shortcut else ""))
            self.list.addItem(item)

        if self._visible_commands:
            self.list.setCurrentRow(0)

    @staticmethod
    def _format_item(cmd: PaletteCommand) -> str:
        """Текст пункта: «label    ⌨ shortcut» + второй строкой hint/group."""
        left = cmd.label
        right_bits: list[str] = []
        if cmd.hint:
            right_bits.append(cmd.hint)
        elif cmd.group:
            right_bits.append(cmd.group)
        if cmd.shortcut:
            right_bits.append(f"⌨ {cmd.shortcut}")
        right = "  ·  ".join(right_bits)
        if right:
            # Общая строка: label + отступ + серое справа (второй строкой
            # не сделать без delegate — используем одну строку с ·).
            return f"{left}    {right}"
        return left

    @Slot()
    def _execute_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        command_id = item.data(Qt.ItemDataRole.UserRole)
        if not command_id:
            return
        cmd = self._registry.get(str(command_id))
        if cmd is None or cmd.callback is None:
            # Ничего не делаем — команда-подсказка без действия.
            self.accept()
            return
        # Закрываем палитру ДО выполнения — callback может открыть
        # другой модальный диалог, и два модальных окна не дружат.
        self.accept()
        # Вызываем через QTimer(0), чтобы палитра успела закрыться.
        QTimer.singleShot(0, cmd.callback)

    # ------------------------------------------------------------------
    # Показ
    # ------------------------------------------------------------------

    def open_fresh(self) -> None:
        """Сбрасывает поиск и показывает палитру с фокусом на вводе."""
        self.search.clear()
        self._refresh("")
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()


__all__ = ["CommandPalette"]
