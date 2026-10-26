"""0.1.3b: история сессии должна сохранять reasoning и tool_calls.

Проверяем, что ChatPanel при завершении ответа собирает ChatMessage
из накопленных частей (_current_assistant_reasoning, _current_assistant_text,
_current_assistant_parts), а не только из TextPart.

Раньше `_on_agent_finish` делал `ChatMessage(role="assistant", content=result)`
— reasoning и tool-части терялись при перезагрузке сессии.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication

from agents.ollama_client import (
    ReasoningPart,
    TextPart,
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
    yield p
    p.deleteLater()


def test_on_agent_finish_saves_reasoning_and_tools(panel):
    """Полный прогон: reasoning → text → tool_call → tool_result."""
    panel._on_thinking("думаю")
    panel._on_chunk("привет")
    panel._on_tool_started("read_file", json.dumps({"path": "a.py"}))
    panel._on_tool_executed("read_file", "ok", False, None)
    panel._on_agent_finish("привет")

    history = panel.session_store.get_history(panel._session_id, limit=10)
    assert len(history) == 1
    msg = history[0]
    assert msg.role == "assistant"
    assert msg.reasoning_content == "думаю"
    assert msg.content == "привет"

    parts = msg.parts
    assert any(isinstance(p, ReasoningPart) for p in parts)
    assert any(isinstance(p, TextPart) and p.text == "привет" for p in parts)
    assert any(isinstance(p, ToolCallPart) for p in parts)
    assert any(isinstance(p, ToolResultPart) for p in parts)


def test_on_agent_finish_without_reasoning(panel):
    """Если reasoning нет — parts содержат только TextPart + tool-части."""
    panel._on_chunk("только текст")
    panel._on_agent_finish("только текст")

    history = panel.session_store.get_history(panel._session_id, limit=10)
    assert len(history) == 1
    msg = history[0]
    assert msg.reasoning_content == ""
    assert msg.content == "только текст"
    assert any(isinstance(p, TextPart) for p in msg.parts)


def test_thinking_accumulates_even_when_ui_hidden(panel):
    """reasoning_content должен сохраняться, даже если _show_reasoning=False."""
    panel.set_show_reasoning(False)
    panel._on_thinking("внутренние мысли")
    panel._on_chunk("ответ")
    panel._on_agent_finish("ответ")

    history = panel.session_store.get_history(panel._session_id, limit=10)
    assert len(history) == 1
    assert history[0].reasoning_content == "внутренние мысли"


def test_cancelled_does_not_save(panel):
    """result == 'Отменено' → сообщение не сохраняется в сессию."""
    panel._on_chunk("частичный")
    panel._on_agent_finish("Отменено")

    history = panel.session_store.get_history(panel._session_id, limit=10)
    assert history == []