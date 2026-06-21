"""Хранение сессий чата в SQLite."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from agents.ollama_client import ChatMessage


class SessionInfo(BaseModel):
    """Информация о сессии."""
    id: str
    project_path: str
    created_at: datetime
    updated_at: datetime


class SessionStore:
    """Хранилище сессий в SQLite."""

    def __init__(self, db_path: Path):
        """
        Инициализация хранилища.

        Args:
            db_path: Путь к SQLite базе данных
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Инициализирует базу данных."""
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
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions (id)
                )
            """)
            conn.commit()
        logger.debug(f"База данных инициализирована: {self.db_path}")

    def create_session(self, project_path: str) -> str:
        """
        Создает новую сессию.

        Args:
            project_path: Путь к проекту

        Returns:
            str: ID сессии
        """
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sessions "
                "(id, project_path, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, project_path, now, now),
            )
            conn.commit()

        logger.info(f"Создана сессия: {session_id}")
        return session_id

    def save_message(self, session_id: str, message: ChatMessage) -> None:
        """
        Сохраняет сообщение в сессию.

        Args:
            session_id: ID сессии
            message: Сообщение
        """
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, message.role, message.content, now),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()

        logger.debug(f"Сохранено сообщение в сессию {session_id}: {message.role}")

    def get_history(self, session_id: str, limit: int = 50) -> list[ChatMessage]:
        """
        Получает историю сообщений сессии.

        Args:
            session_id: ID сессии
            limit: Максимальное количество сообщений

        Returns:
            list[ChatMessage]: Список сообщений
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            )
            rows = cursor.fetchall()

        messages = [ChatMessage(role=row[0], content=row[1]) for row in reversed(rows)]
        logger.debug(f"Загружено сообщений: {len(messages)}")
        return messages

    def list_sessions(self, project_path: str | None = None) -> list[SessionInfo]:
        """
        Получает список сессий.

        Args:
            project_path: Фильтр по проекту (опционально)

        Returns:
            list[SessionInfo]: Список сессий
        """
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
        """Удаляет сессии старше days дней, возвращает количество удалённых."""
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
        return self.get_history(session_id, limit=1000)  # можно без лимита
