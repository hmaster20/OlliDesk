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
from typing import Any

from loguru import logger
from pydantic import BaseModel, TypeAdapter, ValidationError

from agents.ollama_client import ChatMessage, MessagePart
from core import redaction

_PARTS_ADAPTER: TypeAdapter[MessagePart] = TypeAdapter(MessagePart)

def _redact_parts(parts: list[MessagePart]) -> list[dict]:
    """Сериализует части, вырезая секреты из строковых значений (0.1.2).

    Структура JSON сохраняется: ключи и типы не трогаются, редактируются
    только строковые значения. Это гарантирует, что ``json.dumps`` даёт
    валидный JSON, а ``_parse_parts`` может распарсить его обратно.

    Зачем: аргументы ``write_file``/``apply_patch`` и результаты tool-call
    могут содержать секреты (пользователь вставил токен в чат, модель
    сгенерировала ``run_shell_command`` с секретом в env-переменной, …).
    ``tool_registry.execute`` уже редактирует ``content`` перед отдачей в LLM,
    но ``ToolCallPart.arguments`` попадают в сессию без изменений — эта
    функция закрывает дыру.
    """
    out: list[dict] = []
    for p in parts:
        try:
            dumped = p.model_dump(mode="json")
            out.append(_redact_strings(dumped))
        except Exception as e:
            logger.warning(f"SessionStore: не удалось сериализовать part: {e}")
            out.append({})
    return out


def _redact_strings(obj: Any) -> Any:
    """Рекурсивно редактирует только строковые ЗНАЧЕНИЯ (ключи сохраняются)."""
    if isinstance(obj, str):
        return redaction.redact_only(obj)
    if isinstance(obj, dict):
        return {k: _redact_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_strings(v) for v in obj]
    return obj

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
                    tool_call_id TEXT,
                    name TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions (id)
                )
            """)
            # Миграция старых БД: добавляем недостающие колонки.
            # Идемпотентно: `PRAGMA table_info` проверяет наличие перед ALTER.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
            for col, ddl in (
                ("parts_json", "ALTER TABLE messages ADD COLUMN parts_json TEXT"),
                ("tool_call_id", "ALTER TABLE messages ADD COLUMN tool_call_id TEXT"),
                ("name", "ALTER TABLE messages ADD COLUMN name TEXT"),
            ):
                if col in cols:
                    continue
                try:
                    conn.execute(ddl)
                    logger.info(f"SessionStore: миграция messages → добавлена колонка {col}")
                except sqlite3.OperationalError as e:
                    logger.warning(f"SessionStore: не удалось добавить {col}: {e}")
            conn.commit()
        logger.debug(f"База данных инициализирована: {self.db_path}")

    def save_message(self, session_id: str, message: ChatMessage) -> None:
        """Сохраняет сообщение вместе с типизированными частями.

        `content` пишется для обратной совместимости (UI читает строку),
        `parts_json` — источник истины для новых записей. `tool_call_id`
        и `name` — отдельные колонки: они принадлежат ChatMessage, а не
        parts, и раньше терялись при восстановлении истории.

        0.1.2: и `content`, и `parts_json` проходят redaction. Секреты
        (API-ключи, JWT, пароли в URL, env-переменные с секретными именами)
        не попадают на диск. Редактируются только строковые значения
        (ключи/структура сохраняются — JSON остаётся валидным).
        """
        now = datetime.now().isoformat()
        try:
            parts_json: str | None = json.dumps(
                _redact_parts(message.parts),
                ensure_ascii=False,
                default=str,
            )
        except Exception as e:
            logger.warning(f"Не удалось сериализовать parts, сохраняю только content: {e}")
            parts_json = None

        content = redaction.redact_only(message.content) if message.content else message.content

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages "
                "(session_id, role, content, parts_json, tool_call_id, name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    message.role,
                    content,
                    parts_json,
                    message.tool_call_id,
                    message.name,
                    now,
                ),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()

        logger.debug(f"Сохранено сообщение в сессию {session_id}: {message.role}")

    def get_history(self, session_id: str, limit: int = 50) -> list[ChatMessage]:
        """Загружает историю сообщений с восстановлением parts и метаданных tool."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT role, content, parts_json, tool_call_id, name "
                "FROM messages WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            )
            rows = cursor.fetchall()

        messages: list[ChatMessage] = []
        for role, content, parts_json, tool_call_id, name in reversed(rows):
            parts = self._parse_parts(parts_json)
            if parts:
                messages.append(
                    ChatMessage(
                        role=role,
                        parts=parts,
                        tool_call_id=tool_call_id,
                        name=name,
                    )
                )
            else:
                # Fallback: старая запись без parts_json.
                messages.append(
                    ChatMessage(
                        role=role,
                        content=content or "",
                        tool_call_id=tool_call_id,
                        name=name,
                    )
                )
        logger.debug(f"Загружено сообщений: {len(messages)}")
        return messages

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
                    "SELECT id, project_path, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
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
