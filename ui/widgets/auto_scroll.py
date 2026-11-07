"""AutoScrollArea: QScrollArea с «прилипанием к нижней кромке» (9.5).

Поведение:
  * Пользователь у нижней кромки (≤ 32 px) — следуем за стримом
    (setValue(maximum) при каждом append).
  * Пользователь проскроллил вверх — НЕ дёргаем, показываем кнопку
    «▼ Вниз» поверх scroll area.
  * Клик по «▼ Вниз» — возвращаемся к кромке, кнопка скрывается.

Используется в:
  * ChatPanel (основная лента сообщений);
  * ChatThoughtStream (серый трейс);
  * ChatResearchPassport (тело паспорта).

API совместим с QScrollArea; добавляет `scroll_to_bottom_if_sticky()`
вместо прямого `verticalScrollBar().setValue(maximum)`.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QPushButton, QScrollArea, QSizePolicy, QWidget

# Порог «пользователь у нижней кромки» (px). Меньше — считаем, что
# пользователь «прилип» и стрим должен следовать за контентом.
STICK_THRESHOLD_PX = 32


class AutoScrollArea(QScrollArea):
    """QScrollArea с авто-скроллом и кнопкой «▼ Вниз»."""

    # Эмитится при переключении sticky-режима (для диагностики / тестов).
    stick_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stick_to_bottom: bool = True
        self._auto_scroll_enabled: bool = True
        self._down_button: QPushButton | None = None

        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        sb = self.verticalScrollBar()
        sb.valueChanged.connect(self._on_scroll_value_changed)
        sb.rangeChanged.connect(self._on_range_changed)

        # Устанавливаем viewport-фильтр — нам нужны resize-события.
        self.viewport().installEventFilter(self)

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    @property
    def stick_to_bottom(self) -> bool:
        """True, если стрим должен автоматически следовать за контентом."""
        return self._stick_to_bottom

    @property
    def auto_scroll_enabled(self) -> bool:
        """Глобальный выключатель авто-скролла (для отладки/тестов)."""
        return self._auto_scroll_enabled

    def set_auto_scroll_enabled(self, enabled: bool) -> None:
        """Полностью выключает авто-скролл (например, при открытии старой сессии)."""
        self._auto_scroll_enabled = enabled
        if not enabled:
            self._down_button_hide()

    def scroll_to_bottom_if_sticky(self) -> None:
        """Скроллит к нижней кромке, ТОЛЬКО если пользователь уже там."""
        if not self._auto_scroll_enabled or not self._stick_to_bottom:
            return
        sb = self.verticalScrollBar()
        # Используем QTimer(0), чтобы не дёргать layout в середине append.
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    def scroll_to_bottom_force(self) -> None:
        """Принудительно скроллит к нижней кромке (кнопка «▼ Вниз»)."""
        self._stick_to_bottom = True
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._down_button_hide()
        self.stick_changed.emit(True)

    # ------------------------------------------------------------------
    # Внутренние слоты
    # ------------------------------------------------------------------

    @Slot(int)
    def _on_scroll_value_changed(self, value: int) -> None:
        sb = self.verticalScrollBar()
        distance = sb.maximum() - value
        at_bottom = distance <= STICK_THRESHOLD_PX
        if at_bottom != self._stick_to_bottom:
            self._stick_to_bottom = at_bottom
            self.stick_changed.emit(at_bottom)
            if at_bottom:
                self._down_button_hide()
            elif self._auto_scroll_enabled:
                self._down_button_show()

    @Slot(int, int)
    def _on_range_changed(self, _min: int, _max: int) -> None:
        """Когда контент вырос и мы «на дне» — доскролливаем."""
        if self._auto_scroll_enabled and self._stick_to_bottom:
            sb = self.verticalScrollBar()
            sb.setValue(sb.maximum())

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.viewport() and event.type() == QEvent.Type.Resize:
            self._reposition_down_button()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Кнопка «▼ Вниз»
    # ------------------------------------------------------------------

    def _ensure_down_button(self) -> QPushButton:
        if self._down_button is None:
            btn = QPushButton("▼ Вниз", self)
            btn.setObjectName("_auto_scroll_down_button")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet(
                "QPushButton#_auto_scroll_down_button {"
                " background-color: rgba(60, 60, 60, 0.9);"
                " color: #ffffff;"
                " border: 1px solid rgba(255, 255, 255, 0.2);"
                " border-radius: 12px;"
                " padding: 4px 12px;"
                " font-size: 12px;"
                " font-weight: 600;"
                "}"
                "QPushButton#_auto_scroll_down_button:hover {"
                " background-color: rgba(212, 163, 115, 0.95);"
                " color: #1e1e1e;"
                "}"
            )
            btn.clicked.connect(self.scroll_to_bottom_force)
            btn.hide()
            self._down_button = btn
        return self._down_button

    def _down_button_show(self) -> None:
        btn = self._ensure_down_button()
        self._reposition_down_button()
        btn.show()
        btn.raise_()

    def _down_button_hide(self) -> None:
        if self._down_button is not None:
            self._down_button.hide()

    def _reposition_down_button(self) -> None:
        if self._down_button is None or not self._down_button.isVisible():
            return
        btn = self._down_button
        btn.adjustSize()
        margin = 12
        x = self.width() - btn.width() - margin
        y = self.height() - btn.height() - margin
        btn.move(max(0, x), max(0, y))


__all__ = ["AutoScrollArea", "STICK_THRESHOLD_PX"]
