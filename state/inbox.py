"""Durable inbox сессии (2.12): SQLite-очередь запросов.

Запросы пишутся в `pending` **до** запуска `AgentChatThread`. Это
гарантирует, что даже при падении процесса или закрытии окна запрос
не потеряется — при следующем старте `recover()` переведёт «зависшие»
`running` обратно в `pending`, и UI покажет счётчик.

Схема:
session_inbox(
id TEXT PRIMARY KEY, -- uuid4 hex
session_id TEXT NOT NULL,
prompt TEXT NOT NULL,
context_json TEXT, -- AgentContext snapshot (опционально)
mode TEXT NOT NULL, -- chat | plan | agent
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
state TEXT NOT NULL, -- pending | running | done | failed
result_json TEXT, -- финальный ответ (для аудита)
error TEXT
)

text

Принципы:
  * **Один writer**: `claim()` атомарно переводит `pending → running`
    (UPDATE с `WHERE state='pending'` + проверка `rowcount`). SQLite с
    WAL-режимом даёт конкурентный claim: только один вызов получит
    `rowcount=1`.
  * **Recover**: `running → pending` при старте — «процесс упал или
    пользователь закрыл окно». Счётчик `pending` сразу виден в дашборде.
  * **Не более N pending на сессию**: защита от накопления «мусора»
    при зацикливании `enqueue()`.
  * **Retention**: `done`/`failed` старше 7 дней удаляются при старте.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from core.event_bus import emit_event
from core.events import EventKind

# Состояния записи.
InboxState = Literal["pending", "running", "done", "failed"]

# Максимум pending на сессию — защита от «мусора».
MAX_PENDING_PER_SESSION = 20

# Retention для done/failed (дней).
RETENTION_DAYS = 7

# Сколько записей возвращать по умолчанию в `list_*`.
DEFAULT_LIMIT = 50


@dataclass
class InboxItem:
    """Одна запись в inbox."""

    id: str
    session_id: str
    prompt: str
    mode: str
    state: InboxState
    created_at: str
    updated_at: str
    context_json: str = ""
    result_json: str = ""
    error: str = ""


class SessionInbox:
    """SQLite-очередь запросов (2.12).

    Потокобезопасность: одно соединение + ``RLock`` вокруг записей.
    WAL-режим — параллельные чтения во время записи.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        from core.utils import get_app_data_dir

        self.db_path = db_path or (get_app_data_dir() / "sessions.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ------------------------------------------------------------------
    # Схема
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_inbox (
                    id          TEXT PRIMARY KEY,
                    session_id  TEXT NOT NULL,
                    prompt      TEXT NOT NULL,
                    context_json TEXT,
                    mode        TEXT NOT NULL DEFAULT 'chat',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    state       TEXT NOT NULL,
                    result_json TEXT,
                    error       TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_inbox_session ON session_inbox(session_id, state)"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_inbox_state ON session_inbox(state)")

    # ------------------------------------------------------------------
    # enqueue / claim / complete / fail
    # ------------------------------------------------------------------

    def enqueue(
        self,
        session_id: str,
        prompt: str,
        *,
        mode: str = "chat",
        context: dict[str, Any] | None = None,
    ) -> str:
        """Ставит запрос в очередь. Возвращает id.

        Args:
            session_id: ID сессии (`ChatPanel._session_id`).
            prompt: Текст запроса (то, что ввёл пользователь).
            mode: Режим (``chat`` / ``plan`` / ``agent``).
            context: Snapshot контекста (опционально, для аудита).

        Returns:
            id записи (uuid4 hex, 32 символа).
        """
        if not session_id:
            raise ValueError("inbox.enqueue: пустой session_id")
        if not prompt or not prompt.strip():
            raise ValueError("inbox.enqueue: пустой prompt")

        # Проверка лимита pending на сессию.
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS c FROM session_inbox WHERE session_id = ? AND state = 'pending'",
                (session_id,),
            )
            pending = int(cur.fetchone()["c"])
        if pending >= MAX_PENDING_PER_SESSION:
            raise RuntimeError(
                f"inbox: слишком много pending запросов "
                f"({pending}/{MAX_PENDING_PER_SESSION}) для сессии "
                f"{session_id[:12]}…"
            )

        item_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        context_json = json.dumps(context, ensure_ascii=False, default=str) if context else None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO session_inbox"
                " (id, session_id, prompt, context_json, mode,"
                "  created_at, updated_at, state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                (
                    item_id,
                    session_id,
                    prompt.strip(),
                    context_json,
                    mode,
                    now,
                    now,
                ),
            )
        logger.info(
            f"inbox.enqueue: id={item_id[:8]}… session={session_id[:12]}… mode={mode} chars={len(prompt)}"
        )
        emit_event(
            EventKind.INBOX_ENQUEUED.value,
            {"id": item_id, "session_id": session_id, "mode": mode},
        )
        return item_id

    def claim(self, item_id: str) -> bool:
        """Атомарно переводит ``pending → running``.

        Returns:
            ``True``, если удалось (был pending), иначе ``False``.
        """
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE session_inbox SET state = 'running', updated_at = ?"
                " WHERE id = ? AND state = 'pending'",
                (now, item_id),
            )
            ok = cur.rowcount == 1
        if ok:
            logger.debug(f"inbox.claim: id={item_id[:8]}… → running")
        return ok

    def complete(self, item_id: str, result: str = "") -> None:
        """Переводит ``running → done`` с сохранением результата."""
        now = datetime.now().isoformat(timespec="seconds")
        result_json = None
        if result:
            result_json = json.dumps({"result": result[:2000]}, ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE session_inbox SET state = 'done', updated_at = ?, result_json = ? WHERE id = ?",
                (now, result_json, item_id),
            )
        logger.debug(f"inbox.complete: id={item_id[:8]}…")
        emit_event(
            EventKind.INBOX_DELIVERED.value,
            {"id": item_id, "state": "done"},
        )

    def fail(self, item_id: str, error: str) -> None:
        """Переводит ``running → failed`` с сообщением."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE session_inbox SET state = 'failed', updated_at = ?, error = ? WHERE id = ?",
                (now, (error or "")[:1000], item_id),
            )
        logger.info(f"inbox.fail: id={item_id[:8]}… ({error[:120]})")
        emit_event(
            EventKind.INBOX_FAILED.value,
            {"id": item_id, "error": (error or "")[:200]},
        )

    # ------------------------------------------------------------------
    # Recover / cleanup
    # ------------------------------------------------------------------

    def recover(self) -> int:
        """Возвращает «зависшие» ``running → pending`` при старте.

        Returns:
            Сколько записей восстановлено.
        """
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE session_inbox SET state = 'pending', updated_at = ? WHERE state = 'running'",
                (now,),
            )
            count = cur.rowcount
        if count:
            logger.warning(f"inbox.recover: {count} 'running' → 'pending' (упавший процесс?)")
        return count

    def cleanup_old(self, *, retention_days: int = RETENTION_DAYS) -> int:
        """Удаляет ``done``/``failed`` старше ``retention_days``."""
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM session_inbox WHERE state IN ('done', 'failed') AND updated_at < ?",
                (cutoff,),
            )
            count = cur.rowcount
        if count:
            logger.info(f"inbox.cleanup: удалено {count} старых записей")
        return count

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, item_id: str) -> InboxItem | None:
        """Возвращает запись по id."""
        with self._lock:
            cur = self._conn.execute("SELECT * FROM session_inbox WHERE id = ?", (item_id,))
            row = cur.fetchone()
        return _row_to_item(row) if row else None

    def list_pending(self, session_id: str | None = None) -> list[InboxItem]:
        """Список ``pending`` (для UI-индикатора)."""
        return self._list_by_state("pending", session_id)

    def list_running(self, session_id: str | None = None) -> list[InboxItem]:
        """Список ``running`` (для диагностики)."""
        return self._list_by_state("running", session_id)

    def list_session(self, session_id: str, limit: int = DEFAULT_LIMIT) -> list[InboxItem]:
        """Все записи сессии (свежие первыми)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM session_inbox WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            )
            return [_row_to_item(r) for r in cur.fetchall()]

    def _list_by_state(self, state: InboxState, session_id: str | None) -> list[InboxItem]:
        with self._lock:
            if session_id:
                cur = self._conn.execute(
                    "SELECT * FROM session_inbox WHERE state = ? AND session_id = ? ORDER BY created_at ASC",
                    (state, session_id),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM session_inbox WHERE state = ? ORDER BY created_at ASC",
                    (state,),
                )
            return [_row_to_item(r) for r in cur.fetchall()]

    def count_pending(self, session_id: str | None = None) -> int:
        """Быстрый счётчик pending (для дашборда)."""
        with self._lock:
            if session_id:
                cur = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM session_inbox WHERE state = 'pending' AND session_id = ?",
                    (session_id,),
                )
            else:
                cur = self._conn.execute("SELECT COUNT(*) AS c FROM session_inbox WHERE state = 'pending'")
            return int(cur.fetchone()["c"])

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Закрывает соединение."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                logger.debug("SessionInbox.close: ошибка", exc_info=True)


def _row_to_item(row: sqlite3.Row) -> InboxItem:
    return InboxItem(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        prompt=str(row["prompt"]),
        context_json=str(row["context_json"] or ""),
        mode=str(row["mode"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        state=row["state"],  # type: ignore[arg-type]
        result_json=str(row["result_json"] or ""),
        error=str(row["error"]),
    )


__all__ = [
    "InboxItem",
    "InboxState",
    "SessionInbox",
    "MAX_PENDING_PER_SESSION",
    "RETENTION_DAYS",
]
