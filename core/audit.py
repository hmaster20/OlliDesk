"""Аудит вызовов инструментов (0.1.1).

SQLite-журнал в ``~/.ollidesk/audit.db``: одна строка на вызов
инструмента. Хранит **хеши** аргументов и результата (не сами данные),
чтобы не плодить PII и не раздувать БД. Нужен для:

* расследования инцидентов («что делал агент вчера в 15:00»);
* отладки политик («почему команда заблокирована»);
* UI «Безопасность → Журнал инструментов» (см. ``AuditLog.get_recent``).

Пишется из ``run_shell_command`` и в будущем — из всех мутирующих
инструментов (``write_file``, ``git_commit``). Сейчас покрыт только
shell — это самая опасная поверхность.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from core.utils import get_app_data_dir


def _hash(value: Any) -> str:
    """SHA-256 (32 символа hex) от стабильной строки.

    Для dict/list — ``json.dumps(sort_keys=True)``, чтобы одинаковые
    по смыслу аргументы давали одинаковый хеш. Для str — сама строка.
    Любые ошибки сериализации → ``str(value)``.
    """
    try:
        if isinstance(value, str):
            payload = value
        else:
            payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(value)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:32]


@dataclass
class AuditEntry:
    """Один ряд журнала (для ``get_recent``)."""

    id: int
    timestamp: str
    tool: str
    args_hash: str
    policy: str
    approved: bool
    result_hash: str
    error: str
    dangerous: bool


class AuditLog:
    """SQLite-журнал вызовов инструментов.

    Ошибки записи **не пробрасываются** — это аудит, а не критический
    путь. Если БД залочена или диск полон, инструмент работает
    дальше, только ``logger.warning``.
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (get_app_data_dir() / "audit.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    args_hash TEXT NOT NULL,
                    policy TEXT NOT NULL,
                    approved INTEGER NOT NULL,
                    result_hash TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    dangerous INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_audit_timestamp ON audit(timestamp)")
            conn.commit()

    def log(
        self,
        tool_name: str,
        args: Any,
        *,
        policy: str = "auto",
        approved: bool = True,
        result_content: str = "",
        is_error: bool = False,
        dangerous: bool = False,
    ) -> None:
        """Пишет один ряд в журнал. Ошибки записи не пробрасывает."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO audit"
                    " (timestamp, tool, args_hash, policy, approved, result_hash, error, dangerous)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now().isoformat(timespec="seconds"),
                        tool_name,
                        _hash(args),
                        policy,
                        1 if approved else 0,
                        _hash(result_content),
                        "" if not is_error else "error",
                        1 if dangerous else 0,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"audit_log: не удалось записать {tool_name}: {e}")

    def get_recent(self, limit: int = 100) -> list[AuditEntry]:
        """Последние N записей (для UI и тестов)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, timestamp, tool, args_hash, policy, approved,"
                " result_hash, error, dangerous"
                " FROM audit ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
        return [
            AuditEntry(
                id=r[0],
                timestamp=r[1],
                tool=r[2],
                args_hash=r[3],
                policy=r[4],
                approved=bool(r[5]),
                result_hash=r[6],
                error=r[7],
                dangerous=bool(r[8]),
            )
            for r in rows
        ]

    def count(self) -> int:
        """Общее число записей (для тестов)."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM audit")
            return int(cur.fetchone()[0])


# Синглтон для удобства из инструментов. В тестах передаётся
# AuditLog(db_path=tmp_path / "audit.db") напрямую в run_shell_command.
_default_audit_log: AuditLog | None = None


def get_audit_log() -> AuditLog:
    """Возвращает синглтон AuditLog (создаёт при первом вызове)."""
    global _default_audit_log
    if _default_audit_log is None:
        _default_audit_log = AuditLog()
    return _default_audit_log


__all__ = ["AuditEntry", "AuditLog", "get_audit_log", "_hash"]
