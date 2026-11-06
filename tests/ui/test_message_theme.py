"""9.5.1: тема в ChatMessageItem / ToolCallCard / ReasoningWidget."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from ui.chat.message_item import (
    ChatMessageItem,
    ReasoningWidget,
    TextWidget,
    ToolCallCard,
    _action_btn_css,
    _theme_colors,
    render_markdown,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class TestThemeTokens:
    def test_dark_and_light_differ(self) -> None:
        dark = _theme_colors(True)
        light = _theme_colors(False)
        assert dark.bubble_bg != light.bubble_bg
        assert dark.code_bg != light.code_bg
        assert dark.reasoning_bg != light.reasoning_bg

    def test_action_btn_css_contains_tokens(self) -> None:
        css = _action_btn_css(True)
        assert "QPushButton" in css
        assert "border-radius" in css

    def test_action_btn_css_differs_by_theme(self) -> None:
        assert _action_btn_css(True) != _action_btn_css(False)


class TestRenderMarkdown:
    def test_code_block_uses_light_palette(self) -> None:
        html = render_markdown("```python\nx = 1\n```", dark=False)
        # На светлой теме цвет фона — code_bg = #f5f5f5.
        assert "#f5f5f5" in html

    def test_code_block_uses_dark_palette(self) -> None:
        html = render_markdown("```python\nx = 1\n```", dark=True)
        assert "#23232c" in html

    def test_light_uses_friendly_pygments(self) -> None:
        # Разные pygments-стили → разный HTML (по крайней мере, в header
        # цветах). Проверяем, что HTML отличается от тёмного варианта.
        dark_html = render_markdown("```python\nx = 1\n```", dark=True)
        light_html = render_markdown("```python\nx = 1\n```", dark=False)
        assert dark_html != light_html


class TestChatMessageItemTheme:
    def test_initial_dark(self, qapp) -> None:
        item = ChatMessageItem("assistant", "hi", dark=True)
        assert item._dark is True
        item.deleteLater()

    def test_apply_theme_light(self, qapp) -> None:
        item = ChatMessageItem("assistant", "hi", dark=True)
        item.apply_theme(False)
        assert item._dark is False
        # Пузырь перекрашен — стиль содержит bubble_bg из светлой темы.
        style = item.bubble.styleSheet()
        light = _theme_colors(False)
        assert light.bubble_bg in style
        item.deleteLater()

    def test_user_bubble_uses_user_bg(self, qapp) -> None:
        item = ChatMessageItem("user", "hi", dark=True)
        style = item.bubble.styleSheet()
        dark = _theme_colors(True)
        assert dark.bubble_user_bg in style
        item.deleteLater()


class TestToolCallCardTheme:
    def test_initial_dark(self, qapp) -> None:
        card = ToolCallCard("read_file", '{"path": "a.py"}', dark=True)
        assert card._dark is True
        card.deleteLater()

    def test_apply_theme(self, qapp) -> None:
        card = ToolCallCard("read_file", "{}", dark=True)
        card.set_result("ok", is_error=False)
        card.apply_theme(False)
        assert card._dark is False
        # Style контейнера содержит цвета светлой темы.
        style = card._container.styleSheet()
        # На светлой pending/ok/error — другие цвета.
        assert "rgba(80,160,80,0.08)" in style  # ok / light
        card.deleteLater()


class TestReasoningWidgetTheme:
    def test_apply_theme(self, qapp) -> None:
        w = ReasoningWidget("think", dark=True)
        w.apply_theme(False)
        assert w._dark is False
        w.deleteLater()


class TestTextWidgetTheme:
    def test_set_dark_reapplies_style(self, qapp) -> None:
        w = TextWidget("some **markdown**")
        w.set_dark(False)
        # Стиль перекрашен.
        assert _theme_colors(False).text in w.styleSheet()
        w.deleteLater()
