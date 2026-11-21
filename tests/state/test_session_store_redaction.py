"""Тесты 0.1.2: redaction в SessionStore.save_message."""

from __future__ import annotations

from pathlib import Path

from agents.ollama_client import ChatMessage, ToolCall, ToolCallPart
from state.session_store import SessionStore


def test_text_with_secret_is_redacted(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session("proj")
    store.save_message(
        sid,
        ChatMessage(role="user", content="my token is TOKEN=abcdef123456"),
    )
    history = store.get_history(sid)
    assert len(history) == 1
    assert "abcdef123456" not in history[0].content
    assert "[REDACTED:env_secret]" in history[0].content


def test_tool_call_args_are_redacted(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session("proj")
    msg = ChatMessage(
        role="assistant",
        parts=[
            ToolCallPart(
                calls=[
                    ToolCall(
                        name="write_file",
                        arguments={"path": ".env", "content": "API_KEY=abcdefghij"},
                    ),
                ],
            ),
        ],
    )
    store.save_message(sid, msg)
    history = store.get_history(sid)
    assert len(history) == 1
    assert history[0].tool_calls
    args = history[0].tool_calls[0].arguments
    assert "abcdefghij" not in args["content"]
    assert "[REDACTED:env_secret]" in args["content"]
    # Структура сохранена: путь .env остался (это не секрет).
    assert args["path"] == ".env"


def test_redaction_preserves_json_validity(tmp_path: Path) -> None:
    """Если паттерн совпал с ключом JSON-структуры — структура должна остаться."""
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session("proj")
    # Ключ "SECRET_KEY" в args — теперь это field, а не env-строка.
    # В JSON-сериализации он станет {"SECRET_KEY": "..."} — паттерн env_secret
    # захватит `"SECRET_KEY": "abcdefgh"` как env-секрет, НО `_redact_parts`
    # редактирует только значения, ключ остаётся.
    msg = ChatMessage(
        role="assistant",
        parts=[
            ToolCallPart(
                calls=[
                    ToolCall(name="some_tool", arguments={"SECRET_KEY": "abcdefghij"}),
                ],
            ),
        ],
    )
    store.save_message(sid, msg)
    # Не должно упасть при чтении:
    history = store.get_history(sid)
    assert len(history) == 1
    assert history[0].tool_calls
    # И ключ не должен превратиться в "[REDACTED:...]":
    assert "SECRET_KEY" in history[0].tool_calls[0].arguments


def test_get_history_is_stable_after_multiple_saves(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.create_session("proj")
    for i in range(3):
        store.save_message(
            sid,
            ChatMessage(role="user", content=f"iter {i}: TOKEN=abcdef{i}00"),
        )
    history = store.get_history(sid)
    assert len(history) == 3
    for h in history:
        assert "TOKEN=abcdef" not in h.content  # все отредактированы
