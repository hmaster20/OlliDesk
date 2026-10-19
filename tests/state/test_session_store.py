"""Тесты SessionStore: parts_json round-trip + миграция старой БД.

Ключевые инварианты:
* Новые записи сохраняют parts (reasoning/tool_calls не теряются).
* Старые строки без parts_json читаются через content.
* Миграция (`ALTER TABLE` для parts_json) идемпотентна.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agents.ollama_client import (
    ChatMessage,
    ReasoningPart,
    TextPart,
    ToolCall,
    ToolCallPart,
)
from state.session_store import SessionStore


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions.db")


class TestPartsRoundTrip:
    def test_simple_text(self, store: SessionStore) -> None:
        sid = store.create_session("proj")
        store.save_message(sid, ChatMessage(role="user", content="привет"))
        history = store.get_history(sid)
        assert len(history) == 1
        assert history[0].role == "user"
        assert history[0].content == "привет"

    def test_reasoning_preserved(self, store: SessionStore) -> None:
        sid = store.create_session("proj")
        msg = ChatMessage(
            role="assistant",
            parts=[ReasoningPart(text="думаю"), TextPart(text="ответ")],
        )
        store.save_message(sid, msg)
        history = store.get_history(sid)
        assert len(history) == 1
        assert history[0].content == "ответ"
        assert history[0].reasoning_content == "думаю"

    def test_tool_calls_preserved(self, store: SessionStore) -> None:
        sid = store.create_session("proj")
        tc = ToolCall(name="read_file", arguments={"path": "a.py"})
        msg = ChatMessage(
            role="assistant",
            parts=[TextPart(text="зову инструмент"), ToolCallPart(calls=[tc])],
        )
        store.save_message(sid, msg)
        history = store.get_history(sid)
        assert len(history) == 1
        assert history[0].content == "зову инструмент"
        assert history[0].tool_calls is not None
        assert history[0].tool_calls[0].name == "read_file"
        assert history[0].tool_calls[0].arguments == {"path": "a.py"}

    def test_tool_result_message(self, store: SessionStore) -> None:
        """role='tool' с tool_call_id/name сохраняется и читается."""
        sid = store.create_session("proj")
        msg = ChatMessage(
            role="tool",
            content="содержимое файла",
            tool_call_id="call_1",
            name="read_file",
        )
        store.save_message(sid, msg)
        history = store.get_history(sid)
        assert len(history) == 1
        assert history[0].role == "tool"
        assert history[0].tool_call_id == "call_1"
        assert history[0].name == "read_file"
        assert history[0].content == "содержимое файла"

    def test_multiple_messages_order(self, store: SessionStore) -> None:
        sid = store.create_session("proj")
        for i in range(3):
            store.save_message(sid, ChatMessage(role="user", content=f"msg{i}"))
        history = store.get_history(sid)
        assert [m.content for m in history] == ["msg0", "msg1", "msg2"]


class TestMigrationFromOldDB:
    """Старые БД без parts_json читаются через content."""

    def test_old_db_without_parts_json(self, tmp_path: Path) -> None:
        db = tmp_path / "old.db"
        # Создаём «старую» схему без parts_json.
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE sessions ("
                " id TEXT PRIMARY KEY, project_path TEXT NOT NULL,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE messages ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " session_id TEXT NOT NULL, role TEXT NOT NULL,"
                " content TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO sessions VALUES ('s1', 'proj', '2024-01-01', '2024-01-01')"
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) "
                "VALUES ('s1', 'user', 'старое сообщение', '2024-01-01')"
            )
            conn.commit()

        # Открываем через SessionStore → должна пройти миграция.
        store = SessionStore(db)
        history = store.get_history("s1")
        assert len(history) == 1
        assert history[0].content == "старое сообщение"

    def test_migration_idempotent(self, tmp_path: Path) -> None:
        """Повторное открытие SessionStore не падает на ALTER TABLE."""
        db = tmp_path / "s.db"
        SessionStore(db)
        # Второй экземпляр на той же БД — миграция уже сделана.
        SessionStore(db)
        # Третий — тоже нормально.
        SessionStore(db)


class TestPartsSerializationFailure:
    """Битые parts не блокируют загрузку сессии."""

    def test_corrupt_parts_json_falls_back_to_content(
        self, tmp_path: Path
    ) -> None:
        store = SessionStore(tmp_path / "s.db")
        sid = store.create_session("proj")
        # Пишем «нормальное» сообщение, потом портим parts_json вручную.
        store.save_message(sid, ChatMessage(role="user", content="ok"))
        with sqlite3.connect(store.db_path) as conn:
            conn.execute("UPDATE messages SET parts_json = 'not-json'")
            conn.commit()

        history = store.get_history(sid)
        assert len(history) == 1
        # Fallback: читается через content.
        assert history[0].content == "ok"

    def test_unknown_part_kind_skipped(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path / "s.db")
        sid = store.create_session("proj")
        store.save_message(
            sid,
            ChatMessage(role="assistant", parts=[TextPart(text="остаток")]),
        )
        # Подмешиваем невалидную часть.
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE messages SET parts_json = ?",
                (
                    '[{"kind":"alien","text":"x"},{"kind":"text","text":"остаток"}]',
                ),
            )
            conn.commit()

        history = store.get_history(sid)
        assert len(history) == 1
        # Невалидная часть отброшена, валидная — прочитана.
        assert history[0].content == "остаток"


class TestDeleteSession:
    def test_delete_removes_messages(self, store: SessionStore) -> None:
        sid = store.create_session("proj")
        store.save_message(sid, ChatMessage(role="user", content="x"))
        store.delete_session(sid)
        assert store.get_history(sid) == []
        assert store.list_sessions("proj") == []