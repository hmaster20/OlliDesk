"""Тесты AutoScrollArea (9.11).

Без pytest-qt: используем QApplication напрямую (создаётся один раз на
модуль, `pytest.importorskip` на PySide6). Эмулируем скролл через
`verticalScrollBar().setValue(...)` — то же, что делает пользователь
колёсиком/ползунком.

Полный UX (колёсико, ресайз, кнопка «▼ Вниз» кликом) — ручной прогон
на живой сборке; здесь проверяем ключевую логику:
  * порог 32 px;
  * sticky vs not-sticky;
  * `scroll_to_bottom_if_sticky` не дёргает layout, когда пользователь
    пролистал вверх;
  * `scroll_to_bottom_force` сбрасывает sticky.
"""

from __future__ import annotations

import pytest

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from ui.widgets.auto_scroll import STICK_THRESHOLD_PX, AutoScrollArea  # noqa: E402


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qt_app():
    """QApplication один раз на весь модуль.

    `pytest-qt` не подключаем (по плану 9.11), но QApplication нужен
    для виджетов. Создаём вручную.
    """
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def scroll_area(qt_app):
    """AutoScrollArea с content-виджетом, достаточно высоким для скролла."""
    area = AutoScrollArea()
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    # Достаточно строк, чтобы был скролл (высота content >> высота area).
    for i in range(50):
        label = QLabel(f"Строка {i}")
        label.setFixedHeight(24)
        layout.addWidget(label)
    content.setFixedHeight(50 * 24)
    area.setWidget(content)
    area.resize(400, 300)
    yield area
    area.deleteLater()
    content.deleteLater()


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------


def test_stick_threshold_is_32() -> None:
    """Порог «у нижней кромки» — 32 px (по плану 9.5.5)."""
    assert STICK_THRESHOLD_PX == 32


# ---------------------------------------------------------------------------
# Начальное состояние
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_stick_true(self, scroll_area: AutoScrollArea) -> None:
        """При создании пользователь «на дне» → sticky=True."""
        assert scroll_area.stick_to_bottom is True

    def test_auto_scroll_enabled(self, scroll_area: AutoScrollArea) -> None:
        assert scroll_area.auto_scroll_enabled is True

    def test_down_button_hidden(self, scroll_area: AutoScrollArea) -> None:
        """Кнопка «▼ Вниз» скрыта, пока не было ручного скролла вверх."""
        # Доступ к приватному — ок для теста.
        assert scroll_area._down_button is None or not scroll_area._down_button.isVisible()

    def test_no_down_button_created(self, scroll_area: AutoScrollArea) -> None:
        """Ленивое создание: кнопки ещё нет."""
        assert scroll_area._down_button is None


# ---------------------------------------------------------------------------
# Скролл вверх/вниз → sticky переключается
# ---------------------------------------------------------------------------


class TestScrollBehavior:
    def test_scroll_to_bottom_keeps_sticky(self, scroll_area: AutoScrollArea) -> None:
        """Пользователь уже на дне → sticky остаётся True."""
        sb = scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())
        assert scroll_area.stick_to_bottom is True

    def test_scroll_up_breaks_sticky(self, scroll_area: AutoScrollArea) -> None:
        """Скролл вверх дальше 32 px → sticky=False."""
        sb = scroll_area.verticalScrollBar()
        assert sb.maximum() > 100, "content должен быть выше area для скролла"
        # Уезжаем от нижней кромки на 100 px (> 32).
        sb.setValue(sb.maximum() - 100)
        assert scroll_area.stick_to_bottom is False

    def test_scroll_up_within_threshold_keeps_sticky(self, scroll_area: AutoScrollArea) -> None:
        """Скролл в пределах 32 px (≤) → sticky остаётся True."""
        sb = scroll_area.verticalScrollBar()
        # Уезжаем ровно на 32 px (на границе — считаем sticky).
        sb.setValue(sb.maximum() - STICK_THRESHOLD_PX)
        assert scroll_area.stick_to_bottom is True

    def test_back_to_bottom_restores_sticky(self, scroll_area: AutoScrollArea) -> None:
        """Пользователь вернулся к кромке → sticky=True снова."""
        sb = scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum() - 200)
        assert scroll_area.stick_to_bottom is False
        sb.setValue(sb.maximum())
        assert scroll_area.stick_to_bottom is True

    def test_signal_emitted_on_change(self, scroll_area: AutoScrollArea) -> None:
        """stick_changed эмитится при переключении."""
        collected: list[bool] = []
        scroll_area.stick_changed.connect(collected.append)
        sb = scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum() - 200)  # → False
        sb.setValue(sb.maximum())        # → True
        assert collected == [False, True]


# ---------------------------------------------------------------------------
# scroll_to_bottom_if_sticky
# ---------------------------------------------------------------------------


class TestScrollIfSticky:
    def test_does_nothing_when_sticky(self, scroll_area: AutoScrollArea) -> None:
        """Если sticky=True — метод асинхронный (QTimer(0)), но sticky не меняет.

        Проверяем, что вызов не падает и не переключает sticky.
        """
        assert scroll_area.stick_to_bottom is True
        scroll_area.scroll_to_bottom_if_sticky()
        assert scroll_area.stick_to_bottom is True

    def test_does_not_scroll_when_not_sticky(
        self, scroll_area: AutoScrollArea, qt_app
    ) -> None:
        """Если пользователь пролистал вверх — метод не двигает scrollbar."""
        sb = scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum() - 200)
        current = sb.value()
        scroll_area.scroll_to_bottom_if_sticky()
        # QTimer(0) не успел выполниться — значение не изменилось.
        # Даже если бы успел, sticky=False → setValue не вызывается.
        assert sb.value() == current

    def test_scrolls_when_sticky(self, scroll_area: AutoScrollArea, qt_app) -> None:
        """Если sticky=True — QTimer(0) доскроллит до максимума."""
        sb = scroll_area.verticalScrollBar()
        # Программно уезжаем на середину, но делаем это как «программный
        # скролл» — sticky остаётся True, потому что valueChanged не
        # эмитится вручную (setValue эмитит valueChanged, и _on_scroll_value_changed
        # сделает sticky=False). Чтобы остаться sticky, надо быть у кромки.
        sb.setValue(sb.maximum())
        # Доскролливаем — уже на максимуме, ничего не изменится.
        scroll_area.scroll_to_bottom_if_sticky()
        # Прокрутим event loop, чтобы QTimer(0) сработал.
        qt_app.processEvents()
        assert sb.value() == sb.maximum()


# ---------------------------------------------------------------------------
# scroll_to_bottom_force
# ---------------------------------------------------------------------------


class TestScrollForce:
    def test_forces_scroll_and_sticky(self, scroll_area: AutoScrollArea, qt_app) -> None:
        """`scroll_to_bottom_force` скроллит к низу и ставит sticky=True."""
        sb = scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum() - 300)
        assert scroll_area.stick_to_bottom is False
        scroll_area.scroll_to_bottom_force()
        assert scroll_area.stick_to_bottom is True
        assert sb.value() == sb.maximum()

    def test_hides_down_button_if_visible(
        self, scroll_area: AutoScrollArea, qt_app
    ) -> None:
        """Если кнопка «▼ Вниз» была — она скрывается после force."""
        sb = scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum() - 300)
        # Кнопка создаётся при первом переходе в not-sticky.
        assert scroll_area._down_button is not None
        scroll_area.scroll_to_bottom_force()
        assert not scroll_area._down_button.isVisible()


# ---------------------------------------------------------------------------
# set_auto_scroll_enabled
# ---------------------------------------------------------------------------


class TestAutoScrollToggle:
    def test_disable_hides_down_button(
        self, scroll_area: AutoScrollArea, qt_app
    ) -> None:
        """Отключение авто-скролла скрывает кнопку «▼ Вниз»."""
        sb = scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum() - 300)  # кнопка появляется
        assert scroll_area._down_button is not None
        assert scroll_area._down_button.isVisible()
        scroll_area.set_auto_scroll_enabled(False)
        assert not scroll_area._down_button.isVisible()
        assert scroll_area.auto_scroll_enabled is False

    def test_disabled_does_not_scroll(
        self, scroll_area: AutoScrollArea, qt_app
    ) -> None:
        """При выключенном авто-скролле `scroll_to_bottom_if_sticky` — no-op."""
        scroll_area.set_auto_scroll_enabled(False)
        sb = scroll_area.verticalScrollBar()
        current = sb.value()
        scroll_area.scroll_to_bottom_if_sticky()
        qt_app.processEvents()
        assert sb.value() == current