"""Тесты индикатора активности (9.5.4).

Без pytest-qt: проверяем только константы-словари и форматирование строки.
Сам виджет `_set_activity_state` требует QApplication, его поведение
покрывается ручным прогоном (см. 9.11 — pytest-qt не подключаем).
"""

from __future__ import annotations

import pytest

# Копия словарей из ChatPanel (без импорта, чтобы не тянуть Qt).
_ICONS = {
    "idle": "💤",
    "thinking": "🤔",
    "tool": "🔧",
    "streaming": "✍️",
    "error": "❌",
    "cancelled": "⏹",
}
_LABELS = {
    "idle": "Готов к работе",
    "thinking": "Думает…",
    "tool": "Выполняет инструмент…",
    "streaming": "Печатает…",
    "error": "Ошибка",
    "cancelled": "Остановлено",
}


class TestActivityConstants:
    def test_all_states_have_icon_and_label(self) -> None:
        assert set(_ICONS.keys()) == set(_LABELS.keys())
        assert len(_ICONS) == 6  # idle, thinking, tool, streaming, error, cancelled

    @pytest.mark.parametrize("state", list(_ICONS.keys()))
    def test_icon_nonempty(self, state: str) -> None:
        assert _ICONS[state]

    @pytest.mark.parametrize("state", list(_LABELS.keys()))
    def test_label_nonempty(self, state: str) -> None:
        assert _LABELS[state]


class TestActivityMessageFormat:
    @staticmethod
    def _format(state: str, detail: str = "") -> str:
        """Копия формата из ChatPanel._set_activity_state."""
        icon = _ICONS.get(state, "●")
        label = _LABELS.get(state, state)
        return f"{icon} {label} {detail}".strip() if detail else f"{icon} {label}"

    def test_idle(self) -> None:
        assert self._format("idle") == "💤 Готов к работе"

    def test_thinking_with_detail(self) -> None:
        assert self._format("thinking", "поиск") == "🤔 Думает… поиск"

    def test_streaming(self) -> None:
        assert self._format("streaming") == "✍️ Печатает…"

    def test_tool_with_name(self) -> None:
        assert self._format("tool", "read_file") == "🔧 Выполняет инструмент… read_file"

    def test_error_with_detail(self) -> None:
        assert self._format("error", "timeout") == "❌ Ошибка timeout"

    def test_unknown_state(self) -> None:
        assert self._format("unknown") == "● unknown"

    def test_empty_detail_no_trailing_space(self) -> None:
        # Никаких висячих пробелов. Проверяем и текст, и отсутствие хвоста.
        result = self._format("idle", "")
        assert result == "💤 Готов к работе", repr(result)
        assert not result.endswith(" "), repr(result)


# Копия словаря цветов из ChatPanel (без импорта Qt).
_STOP_BUTTON_BG = {
    "idle": "#d32f2f",
    "thinking": "#d4a373",
    "tool": "#6ba3d6",
    "streaming": "#98c379",
    "cancelled": "#7a7a8a",
    "error": "#c0392b",
}


class TestStopButtonColors:
    """[9.5.4] Цвета фона кнопки Stop по состоянию."""

    def test_all_states_have_color(self) -> None:
        assert set(_STOP_BUTTON_BG.keys()) == set(_ICONS.keys())
        assert len(_STOP_BUTTON_BG) == 6

    @pytest.mark.parametrize("state", list(_STOP_BUTTON_BG.keys()))
    def test_color_is_hex(self, state: str) -> None:
        color = _STOP_BUTTON_BG[state]
        assert color.startswith("#")
        assert len(color) == 7  # #rrggbb
        # Проверяем, что это валидный hex.
        int(color[1:], 16)

    def test_colors_are_distinct(self) -> None:
        """Все цвета разные — иначе состояния визуально неразличимы."""
        assert len(set(_STOP_BUTTON_BG.values())) == len(_STOP_BUTTON_BG)
