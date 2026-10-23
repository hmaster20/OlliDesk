"""Placeholder для свёрнутых старых сообщений чата (9.10).

Заменяет группу «старых» виджетов (`ChatMessageItem`, `ChatActivityItem`,
`ChatThoughtStream` и т.п.), когда их количество в layout превышает
порог. Виджеты остаются в памяти, но скрыты и удалены из layout —
`QScrollArea` не тратит время на их `sizeHint()`/`paintEvent`.

Клик по placeholder разворачивает порцию (`_RESTORE_BATCH`) самых
«свежих» из свёрнутых — чтобы пользователь видел продолжение, а не
самое начало. Повторный клик — ещё порцию.

`ChatPanel` управляет жизненным циклом: создаёт placeholder при
сворачивании, удаляет после полного разворота.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)


class CollapsedPlaceholder(QWidget):
    """Строка «📜 N сообщений — ▸ показать» для свёрнутой группы."""

    restore_requested = Signal(object)  # self

    def __init__(
        self,
        collapsed_widgets: list[QWidget],
        parent: QWidget | None = None,
    ) -> None:
        """Создаёт placeholder.

        Args:
            collapsed_widgets: Скрытые виджеты в хронологическом порядке
                (самый старый — первый). При `restore` они вернутся в
                layout после placeholder'а.
            parent: Родительский виджет.
        """
        super().__init__(parent)
        self._collapsed: list[QWidget] = list(collapsed_widgets)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background: transparent;")
        self._build_ui()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    @property
    def collapsed_count(self) -> int:
        """Сколько виджетов ещё скрыто."""
        return len(self._collapsed)

    def pop_restore_batch(self, batch_size: int) -> list[QWidget]:
        """Забирает порцию последних (самых «свежих») виджетов.

        Возвращает список виджетов в хронологическом порядке (первый —
        самый старый в порции). После вызова `collapsed_count` уменьшается
        на `len(результата)`.

        Args:
            batch_size: Сколько виджетов развернуть (<= 0 — все).

        Returns:
            Список виджетов для вставки в layout после placeholder'а.
        """
        if not self._collapsed:
            return []
        n = len(self._collapsed)
        if batch_size <= 0 or batch_size >= n:
            take = list(self._collapsed)
            self._collapsed.clear()
        else:
            take = self._collapsed[-batch_size:]
            self._collapsed = self._collapsed[:-batch_size]
        self._refresh_label()
        return take

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 4, 16, 4)
        layout.setSpacing(6)

        self._icon = QLabel("📜")
        self._icon.setStyleSheet("font-size: 14px;")
        layout.addWidget(self._icon)

        self._label = QLabel("")
        self._label.setStyleSheet("color: #7a7a8a; font-size: 12px;")
        layout.addWidget(self._label, stretch=1)

        self._btn = QPushButton("▸ показать")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #444;"
            " border-radius: 10px; color: #6ba3d6; font-size: 11px;"
            " padding: 2px 10px; }"
            " QPushButton:hover { background: rgba(107,163,214,0.15);"
            " color: #8bc0f0; }"
        )
        self._btn.clicked.connect(self._on_click)
        layout.addWidget(self._btn)

        # Общая рамка-«строка» — пунктирная линия сверху и снизу.
        self._frame = QFrame(self)
        self._frame.setStyleSheet(
            "QFrame { border-top: 1px dashed #3a3a48;"
            " border-bottom: 1px dashed #3a3a48; background: transparent; }"
        )
        self._frame.setGeometry(0, 0, 0, 0)

        self._refresh_label()

    def _refresh_label(self) -> None:
        n = len(self._collapsed)
        # Русская форма множественного числа.
        word = _plural_ru(n, "сообщение", "сообщения", "сообщений")
        self._label.setText(f"{n} {word} скрыто")
        self._btn.setVisible(n > 0)

    def _on_click(self) -> None:
        self.restore_requested.emit(self)


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Возвращает правильную форму множественного числа для русского.

    Пример: _plural_ru(1, "сообщение", "сообщения", "сообщений") → "сообщение".
    _plural_ru(5, ...) → "сообщений". _plural_ru(21, ...) → "сообщение".
    """
    n_abs = abs(n) % 100
    last = n_abs % 10
    if 11 <= n_abs <= 14:
        return many
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


__all__ = ["CollapsedPlaceholder", "_plural_ru"]