"""Хранение сессий чата в SQLite.

Схема v2: к `content` добавлена колонка `parts_json` — сериализованный
`list[MessagePart]`. Старые строки без `parts_json` читаются через `content`
(как `ChatMessage(role=..., content=...)`). Миграция идемпотентная: `ALTER
TABLE` выполняется только если колонки ещё нет.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, TypeAdapter, ValidationError

from agents.ollama_client import ChatMessage, MessagePart

_PARTS_ADAPTER: TypeAdapter[MessagePart] = TypeAdapter(MessagePart)


class SessionInfo(BaseModel):
    """Информация о сессии."""

    id: str
    project_path: str
    created_at: datetime
    updated_at: datetime


class SessionStore:
    """Хранилище сессий в SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    parts_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions (id)
                )
            """)
            # Миграция старых БД: добавляем parts_json, если её нет.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
            if "parts_json" not in cols:
                try:
                    conn.execute("ALTER TABLE messages ADD COLUMN parts_json TEXT")
                    logger.info("SessionStore: миграция messages → добавлена колонка parts_json")
                except sqlite3.OperationalError as e:
                    logger.warning(f"SessionStore: не удалось добавить parts_json: {e}")
            conn.commit()
        logger.debug(f"База данных инициализирована: {self.db_path}")

    def create_session(self, project_path: str) -> str:
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sessions (id, project_path, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, project_path, now, now),
            )
            conn.commit()
        logger.info(f"Создана сессия: {session_id}")
        return session_id

    def save_message(self, session_id: str, message: ChatMessage) -> None:
        """Сохраняет сообщение вместе с типизированными частями.

        Пишет ``message.content`` (строку) в `content` для обратной совместимости
        и ``json.dumps(parts)`` в `parts_json` — источник истины для новых записей.
        """
        now = datetime.now().isoformat()
        try:
            parts_json: str | None = json.dumps(
                [p.model_dump(mode="json") for p in message.parts],
                ensure_ascii=False,
                default=str,
            )
        except Exception as e:
            logger.warning(f"Не удалось сериализовать parts, сохраняю только content: {e}")
            parts_json = None

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages "
                "(session_id, role, content, parts_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, message.role, message.content, parts_json, now),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()

        logger.debug(f"Сохранено сообщение в сессию {session_id}: {message.role}")

    @staticmethod
    def _parse_parts(raw: str | None) -> list[MessagePart]:
        """Разбирает `parts_json` обратно в список MessagePart.

        Невалидные части пропускаются с warning — старые/битые записи не
        должны блокировать загрузку сессии. Пустой результат означает
        fallback на `content`.
        """
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"SessionStore: parts_json не разбирается: {e}")
            return []
        if not isinstance(data, list):
            return []
        parts: list[MessagePart] = []
        for item in data:
            try:
                parts.append(_PARTS_ADAPTER.validate_python(item))
            except ValidationError as e:
                logger.warning(f"SessionStore: пропускаю невалидную part: {e}")
        return parts

    def get_history(self, session_id: str, limit: int = 50) -> list[ChatMessage]:
        """Загружает историю сообщений с восстановлением parts (если есть)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT role, content, parts_json FROM messages "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            )
            rows = cursor.fetchall()

        messages: list[ChatMessage] = []
        for role, content, parts_json in reversed(rows):
            parts = self._parse_parts(parts_json)
            if parts:
                messages.append(ChatMessage(role=role, parts=parts))
            else:
                # Fallback: старая запись без parts_json.
                messages.append(ChatMessage(role=role, content=content or ""))
        logger.debug(f"Загружено сообщений: {len(messages)}")
        return messages

    def list_sessions(self, project_path: str | None = None) -> list[SessionInfo]:
        with sqlite3.connect(self.db_path) as conn:
            if project_path:
                cursor = conn.execute(
                    "SELECT id, project_path, created_at, updated_at FROM sessions "
                    "WHERE project_path = ? ORDER BY updated_at DESC",
                    (project_path,),
                )
            else:
                cursor = conn.execute(
                    "SELECT id, project_path, created_at, updated_at FROM sessions "
                    "ORDER BY updated_at DESC"
                )
            rows = cursor.fetchall()

        sessions = [
            SessionInfo(
                id=row[0],
                project_path=row[1],
                created_at=datetime.fromisoformat(row[2]),
                updated_at=datetime.fromisoformat(row[3]),
            )
            for row in rows
        ]
        logger.debug(f"Найдено сессий: {len(sessions)}")
        return sessions

    def delete_session(self, session_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()

    def delete_old_sessions(self, days: int) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT id FROM sessions WHERE updated_at < ?", (cutoff,))
            ids = [row[0] for row in cur.fetchall()]
            for sid in ids:
                conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            conn.commit()
        return len(ids)

    def get_session_messages(self, session_id: str) -> list[ChatMessage]:
        return self.get_history(session_id, limit=1000)