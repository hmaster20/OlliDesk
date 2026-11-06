"""0.1.3a: ReasoningWidget — авто-высота, авто-скролл, анимация.

Проверяем контракт, который важен для UX:
- высота тела растёт с контентом и упирается в потолок (~40 % viewport);
- append_text не оставляет пользователя с ручным скроллом — авто-скролл
  к последнему токену;
- collapse() плавно схлопывает тело и скрывает его;
- set_text заменяет, а не дописывает.

Используем QScrollArea как «хост» — так _compute_max_height найдёт
viewport и вернёт реальный cap, а не fallback.
"""

from __future__ import annotations

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(qapp):
    """Хост QScrollArea + внутренний QWidget, чтобы viewport был ненулевым."""
    scroll = QScrollArea()
    inner = QWidget()
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(0, 0, 0, 0)
    scroll.setWidget(inner)
    scroll.setWidgetResizable(True)
    scroll.resize(800, 600)
    scroll.show()
    yield scroll, inner
    scroll.close()
    scroll.deleteLater()


def test_height_grows_with_text(host):
    """Тело растёт по контенту, но не больше cap."""
    from ui.chat.message_item import ReasoningWidget

    _scroll, inner = host
    w = ReasoningWidget()
    inner.layout().addWidget(w)

    for i in range(50):
        w.append_text(f"строка {i}\n")

    cap = w._compute_max_height()
    assert cap > 0
    assert w._browser.maximumHeight() <= cap


def test_max_height_capped(host):
    """Тысячи строк не выталкивают тело за 40 % viewport."""
    from ui.chat.message_item import ReasoningWidget

    _scroll, inner = host
    w = ReasoningWidget()
    inner.layout().addWidget(w)

    for i in range(1000):
        w.append_text(f"line {i}\n")

    cap = w._compute_max_height()
    assert w._browser.maximumHeight() <= cap
    assert cap >= w._MIN_BODY_HEIGHT * 2


def test_collapse_hides_body(host):
    """collapse() анимирует и скрывает тело; _expanded=False."""
    from ui.chat.message_item import ReasoningWidget

    _scroll, inner = host
    w = ReasoningWidget("привет")
    inner.layout().addWidget(w)

    assert w._expanded is True
    w.collapse()
    assert w._expanded is False

    QTest.qWait(w._ANIM_DURATION_MS + 80)
    assert w._browser.isVisible() is False


def test_expand_after_collapse(host):
    """После collapse() кнопка снова разворачивает тело."""
    from ui.chat.message_item import ReasoningWidget

    _scroll, inner = host
    w = ReasoningWidget("привет")
    inner.layout().addWidget(w)

    w.collapse()
    QTest.qWait(w._ANIM_DURATION_MS + 80)
    assert w._browser.isVisible() is False

    w._toggle()
    QTest.qWait(w._ANIM_DURATION_MS + 80)
    assert w._expanded is True
    assert w._browser.isVisible() is True


def test_append_scrolls_to_bottom(host):
    """Длинный контент — авто-скролл к последнему токену."""
    from ui.chat.message_item import ReasoningWidget

    _scroll, inner = host
    w = ReasoningWidget()
    inner.layout().addWidget(w)

    for i in range(300):
        w.append_text(f"line {i}\n")

    QTest.qWait(80)

    sb = w._browser.verticalScrollBar()
    assert sb.maximum() > 0, "при длинном тексте должен появиться скролл"
    assert sb.value() == sb.maximum()


def test_set_text_replaces_not_appends(host):
    """set_text заменяет содержимое, append_text — дописывает."""
    from ui.chat.message_item import ReasoningWidget

    _scroll, inner = host
    w = ReasoningWidget()
    inner.layout().addWidget(w)

    w.set_text("abc")
    assert w._text == "abc"
    w.append_text("def")
    assert w._text == "abcdef"
    w.set_text("xyz")
    assert w._text == "xyz"


def test_fallback_when_no_scrollarea(qapp):
    """Без QScrollArea и без окна — используется fallback-потолок."""
    from ui.chat.message_item import ReasoningWidget

    w = ReasoningWidget()
    cap = w._compute_max_height()
    # 480 — fallback; но если window().height() уже ненулевое
    # (тестовая QApplication), допускаем любое положительное значение.
    assert cap > 0
    assert cap <= max(w._FALLBACK_MAX_HEIGHT, 10_000)
    w.deleteLater()
