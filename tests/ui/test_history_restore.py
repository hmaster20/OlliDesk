"""0.1.3c: load_history восстанавливает reasoning и tool_cards из parts."""

from __future__ import annotations

import contextlib

import pytest
from PySide6.QtWidgets import QApplication

from agents.ollama_client import (
    ChatMessage,
    ReasoningPart,
    TextPart,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
)
from state.session_store import SessionStore


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp, tmp_path):
    from ui.chat.panel import ChatPanel

    store = SessionStore(tmp_path / "sessions.db")
    p = ChatPanel(session_store=store, base_url="http://localhost:11434", model="test")
    p.set_session_id(store.create_session("global"))
    # См. комментарий в test_history_save.py — изолируем GPU от QWebEngine-тестов.
    p._detect_backends = lambda: None  # type: ignore[method-assign]
    yield p
    with contextlib.suppress(Exception):
        p.shutdown()
    p.deleteLater()


def test_load_history_restores_parts(panel):
    """reasoning + text + tool_call + tool_result восстанавливаются в виджет."""
    history = [
        ChatMessage(
            role="assistant",
            parts=[
                ReasoningPart(text="думаю"),
                TextPart(text="привет"),
                ToolCallPart(calls=[ToolCall(name="read_file", arguments={"path": "a.py"})]),
                ToolResultPart(call_id="", name="read_file", content="ok", is_error=False),
            ],
        ),
    ]
    panel.load_history(history)

    from ui.chat.message_item import ChatMessageItem

    widgets = panel.findChildren(ChatMessageItem)
    assert widgets
    w = widgets[0]
    assert w._reasoning_widget is not None
    assert w._text_widget is not None
    assert len(w._tool_cards) == 1

    card = w._tool_cards[0]
    assert card.name == "read_file"
    assert card.state == "ok"
    assert card._result_content == "ok"


def test_load_history_backward_compat_content_only(panel):
    """Старые сообщения без parts (content-only) рендерятся текстом."""
    history = [ChatMessage(role="assistant", content="просто текст")]
    panel.load_history(history)

    from ui.chat.message_item import ChatMessageItem

    widgets = panel.findChildren(ChatMessageItem)
    assert widgets
    assert widgets[0].content == "просто текст"


def test_load_history_multiple_tools(panel):
    """Два вызова подряд: результаты привязываются к своим карточкам."""
    history = [
        ChatMessage(
            role="assistant",
            parts=[
                ToolCallPart(calls=[ToolCall(name="read_file", arguments={"path": "a.py"})]),
                ToolResultPart(call_id="", name="read_file", content="A", is_error=False),
                ToolCallPart(calls=[ToolCall(name="read_file", arguments={"path": "b.py"})]),
                ToolResultPart(call_id="", name="read_file", content="B", is_error=False),
            ],
        ),
    ]
    panel.load_history(history)

    from ui.chat.message_item import ChatMessageItem

    widgets = panel.findChildren(ChatMessageItem)
    assert widgets
    w = widgets[0]
    assert len(w._tool_cards) == 2
    assert w._tool_cards[0]._result_content == "A"
    assert w._tool_cards[1]._result_content == "B"


def test_load_history_empty(panel):
    """Пустая история — не падаем."""
    panel.load_history([])
