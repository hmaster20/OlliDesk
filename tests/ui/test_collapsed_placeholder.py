"""Тесты placeholder'а свёрнутых сообщений (9.10).

Основная логика, которую можно проверить без полноценного GUI-прогона:
  * русская форма множественного числа (`_plural_ru`);
  * `pop_restore_batch` — правильный порядок и количество;
  * `collapsed_count` — счётчик.

Полный UX (клик → разворот → обновление layout) — ручной прогон на
живой сборке (9.11 — `pytest-qt` не подключаем).
"""

from __future__ import annotations

import pytest

from ui.widgets.collapsed_placeholder import _plural_ru


class TestPluralRu:
    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            # Один.
            (1, "сообщение"),
            (21, "сообщение"),
            (31, "сообщение"),
            (101, "сообщение"),
            # Несколько (2-4).
            (2, "сообщения"),
            (3, "сообщения"),
            (4, "сообщения"),
            (22, "сообщения"),
            (23, "сообщения"),
            (24, "сообщения"),
            # Много (0, 5-20, 11-14).
            (0, "сообщений"),
            (5, "сообщений"),
            (6, "сообщений"),
            (10, "сообщений"),
            (11, "сообщений"),
            (12, "сообщений"),
            (13, "сообщений"),
            (14, "сообщений"),
            (15, "сообщений"),
            (20, "сообщений"),
            (25, "сообщений"),
            (100, "сообщений"),
            (111, "сообщений"),
        ],
    )
    def test_cases(self, n: int, expected: str) -> None:
        assert _plural_ru(n, "сообщение", "сообщения", "сообщений") == expected

    def test_negative(self) -> None:
        # abs(-1) % 100 == 1 → "сообщение".
        assert _plural_ru(-1, "сообщение", "сообщения", "сообщений") == "сообщение"
        assert _plural_ru(-5, "сообщение", "сообщения", "сообщений") == "сообщений"


# Логика `pop_restore_batch` требует создания QWidget — тестируем через
# pytest.importorskip + QApplication fixture. Если Qt нет — skip.
@pytest.fixture(scope="module")
def qt_app():
    """QApplication для тестов виджетов (создаётся один раз на модуль)."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class TestPopRestoreBatch:
    def _make_placeholder(self, count: int):
        pytest.importorskip("PySide6")
        from PySide6.QtWidgets import QWidget

        from ui.widgets.collapsed_placeholder import CollapsedPlaceholder

        widgets = [QWidget() for _ in range(count)]
        return CollapsedPlaceholder(widgets), widgets

    def test_full_restore(self, qt_app) -> None:
        ph, widgets = self._make_placeholder(10)
        assert ph.collapsed_count == 10
        batch = ph.pop_restore_batch(0)  # 0 = все
        assert len(batch) == 10
        assert ph.collapsed_count == 0
        # Порядок сохранён: первый — самый старый.
        for i, w in enumerate(batch):
            assert w is widgets[i]

    def test_batch_restore_newest(self, qt_app) -> None:
        ph, widgets = self._make_placeholder(10)
        # Разворачиваем 3 — самые «свежие» (последние в списке).
        batch = ph.pop_restore_batch(3)
        assert len(batch) == 3
        # Это widgets[7], widgets[8], widgets[9] в хронологическом порядке.
        assert batch[0] is widgets[7]
        assert batch[1] is widgets[8]
        assert batch[2] is widgets[9]
        assert ph.collapsed_count == 7

    def test_batch_larger_than_count(self, qt_app) -> None:
        ph, _widgets = self._make_placeholder(5)
        batch = ph.pop_restore_batch(100)
        assert len(batch) == 5
        assert ph.collapsed_count == 0

    def test_empty(self, qt_app) -> None:
        ph, _widgets = self._make_placeholder(0)
        assert ph.collapsed_count == 0
        batch = ph.pop_restore_batch(10)
        assert batch == []

    def test_sequential_batches(self, qt_app) -> None:
        ph, widgets = self._make_placeholder(10)
        b1 = ph.pop_restore_batch(3)  # widgets[7..9]
        b2 = ph.pop_restore_batch(3)  # widgets[4..6]
        b3 = ph.pop_restore_batch(10)  # widgets[0..3] (все оставшиеся)
        assert len(b1) == 3
        assert len(b2) == 3
        assert len(b3) == 4
        assert ph.collapsed_count == 0
        # Восстановленный порядок: b3 + b2 + b1 = widgets[0..9] по порядку.
        assert b3 + b2 + b1 == widgets
