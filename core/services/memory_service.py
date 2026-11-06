"""Эпизодическая память агента (4.3).

`MemoryService` хранит три типа записей в `~/.ollidesk/memory.db`:

* ``reflections`` — короткие рефлексии после завершения задачи:
  «что сработало / что не сработало / итог». Основной источник
  контекста для следующих задач (через FTS5-поиск).
* ``decisions`` — архитектурные решения с обоснованием. Полезны,
  когда модель снова сталкивается с похожей развилкой.
* ``preferences`` — key-value: «пользователь предпочитает X».
  Пример: ``{"prefer_apply_patch": "true"}``.

Принципы:
  * **Локально.** Никаких сетевых вызовов. Шифрование — опционально
    (``MemoryConfig.encrypt`` — заглушка, реальное шифрование вне scope 4.3).
  * **Пользователь контролирует.** `list_recent`, `forget`, `clear`,
    `export_json` — всё через UI (`ui/dialogs/memory_dialog.py`).
  * **FTS5.** Полнотекстовый поиск по `summary` и `tags` (BM25-ранжирование).
    Индекс синхронизируется триггерами SQLite, ручной пересборки не требуется.
  * **Recall.** При старте задачи — `recall(query, top_k)` → top-K рефлексий
    подмешиваются в `_build_system_prompt` в блок «Память». Это даёт
    «агент помнит, что делал вчера».

Почему не в `VectorStore`: эпизодическая память — это десятки-сотни
записей, FTS5 на порядок дешевле и не требует эмбеддингов. При росте
>1000 записей — переключимся на `VectorStore` (тот же API recall).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from core.event_bus import emit_event
from core.events import EventKind
from core.utils import get_app_data_dir

# Максимальные размеры текстовых полей (защита от «мусора»).
MAX_SUMMARY_CHARS = 4000
MAX_RATIONALE_CHARS = 2000

# Сколько записей в recalls отдаём по умолчанию.
DEFAULT_RECALL_TOP_K = 3

# Через сколько часов запись считается «устаревшей» для recall.
# Не влияет на хранение — только на приоритет в выдаче.
DEFAULT_RECALL_MAX_AGE_DAYS = 90


OutcomeType = Literal["success", "partial", "failure"]
TaskType = Literal["research", "code", "bug_fix", "refactor", "chat", "other"]


@dataclass
class Reflection:
    """Одна рефлексия после задачи."""

    id: int | None
    created_at: str
    project_path: str
    task_type: TaskType
    summary: str
    outcome: OutcomeType
    tags: list[str] = field(default_factory=list)


@dataclass
class Decision:
    """Архитектурное решение с обоснованием."""

    id: int | None
    created_at: str
    project_path: str
    decision: str
    rationale: str


@dataclass
class Preference:
    """Пользовательская настройка (key-value)."""

    key: str
    value: str
    updated_at: str


@dataclass
class RecallResult:
    """Результат recall: top-K рефлексий + признак «что-то нашли»."""

    reflections: list[Reflection]
    hit: bool

    def render_for_prompt(self, max_chars: int = 1200) -> str:
        """Форматирует рефлексии для подмешивания в system-промпт.

        Args:
            max_chars: Максимальная длина результата. Если не помещается —
                обрезаем хвост последних рефлексий.

        Returns:
            Текст с маркерами ``[M1]``, ``[M2]`` (Memory), удобными для
            последующей навигации/verifier.
        """
        if not self.reflections:
            return ""
        parts: list[str] = []
        used = 0
        for i, r in enumerate(self.reflections, 1):
            # Компактный формат: «[M1] (code, success) — summary».
            tags_str = f" [{', '.join(r.tags)}]" if r.tags else ""
            block = f"[M{i}] ({r.task_type}, {r.outcome}){tags_str}\n{r.summary}"
            if used + len(block) > max_chars:
                remaining = max_chars - used
                if remaining < 80:
                    break
                block = block[: remaining - 3] + "…"
                parts.append(block)
                break
            parts.append(block)
            used += len(block) + 2  # +2 на '\n\n'
        return "\n\n".join(parts)


class MemoryService:
    """SQLite + FTS5 эпизодическая память (4.3).

    Потокобезопасность: одно соединение с ``check_same_thread=False``
    + ``threading.RLock`` вокруг записей. SQLite в WAL-режиме — чтения
    не блокируют запись, но мы всё равно сериализуем, чтобы избежать
    редких ``SQLITE_BUSY`` на старых Windows.

    Схема создаётся в `__init__` (idempotent). FTS5-индекс — виртуальная
    таблица `reflections_fts`, синхронизируется триггерами.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (get_app_data_dir() / "memory.db")
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
            # WAL — параллельные чтения во время записи.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reflections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    project_path TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_reflections_project ON reflections(project_path)"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_reflections_created ON reflections(created_at)")

            # FTS5 — поиск по summary + tags (BM25).
            # `content=` — external content table: храним данные в
            # reflections, FTS5 только индексирует. Экономит место.
            try:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS reflections_fts
                    USING fts5(
                        summary, tags,
                        content='reflections',
                        content_rowid='id',
                        tokenize='unicode61 remove_diacritics 2'
                    )
                    """
                )
                self._conn.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS reflections_ai
                    AFTER INSERT ON reflections BEGIN
                        INSERT INTO reflections_fts(rowid, summary, tags)
                        VALUES (new.id, new.summary, new.tags);
                    END;
                    CREATE TRIGGER IF NOT EXISTS reflections_ad
                    AFTER DELETE ON reflections BEGIN
                        INSERT INTO reflections_fts(reflections_fts, rowid, summary, tags)
                        VALUES ('delete', old.id, old.summary, old.tags);
                    END;
                    CREATE TRIGGER IF NOT EXISTS reflections_au
                    AFTER UPDATE ON reflections BEGIN
                        INSERT INTO reflections_fts(reflections_fts, rowid, summary, tags)
                        VALUES ('delete', old.id, old.summary, old.tags);
                        INSERT INTO reflections_fts(rowid, summary, tags)
                        VALUES (new.id, new.summary, new.tags);
                    END;
                    """
                )
            except sqlite3.OperationalError as e:
                # FTS5 может быть недоступен в очень старых сборках SQLite.
                # Тогда recall деградирует до LIKE-поиска по summary.
                logger.warning(f"FTS5 недоступен ({e}); recall будет использовать LIKE")

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    project_path TEXT NOT NULL DEFAULT '',
                    decision TEXT NOT NULL,
                    rationale TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_decisions_project ON decisions(project_path)")

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _tx(self):
        with self._lock, self._conn:
            yield self._conn

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    # ------------------------------------------------------------------
    # Reflection: запись / чтение / удаление
    # ------------------------------------------------------------------

    def remember(self, reflection: Reflection) -> int:
        """Сохраняет рефлексию, возвращает её id.

        Args:
            reflection: Запись. ``id`` игнорируется (всегда INSERT).
                ``created_at`` — если пусто, ставится текущее время.

        Returns:
            id вставленной записи.
        """
        summary = self._truncate(reflection.summary, MAX_SUMMARY_CHARS)
        if not summary:
            raise ValueError("reflection.summary пуст — нечего сохранять")
        tags_json = json.dumps(
            [t.strip() for t in reflection.tags if t.strip()],
            ensure_ascii=False,
        )
        created_at = reflection.created_at or self._now()
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO reflections"
                " (created_at, project_path, task_type, summary, outcome, tags)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    created_at,
                    reflection.project_path,
                    reflection.task_type,
                    summary,
                    reflection.outcome,
                    tags_json,
                ),
            )
            new_id = int(cur.lastrowid or 0)

        emit_event(
            EventKind.MEMORY_REMEMBERED.value,
            {
                "id": new_id,
                "task_type": reflection.task_type,
                "outcome": reflection.outcome,
                "tags": reflection.tags,
            },
        )
        logger.info(
            f"Memory: remember id={new_id} ({reflection.task_type}/"
            f"{reflection.outcome}), chars={len(summary)}"
        )
        return new_id

    def forget(self, reflection_id: int) -> bool:
        """Удаляет рефлексию по id. ``False``, если такой не было."""
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM reflections WHERE id = ?", (reflection_id,))
            removed = cur.rowcount > 0
        if removed:
            logger.info(f"Memory: forget id={reflection_id}")
        return removed

    def list_recent(
        self,
        limit: int = 20,
        *,
        project_path: str | None = None,
    ) -> list[Reflection]:
        """Возвращает последние рефлексии (свежие — первыми).

        Args:
            limit: Максимум записей.
            project_path: Если задан — только записи этого проекта.
        """
        with self._lock:
            if project_path:
                cur = self._conn.execute(
                    "SELECT * FROM reflections WHERE project_path = ? ORDER BY id DESC LIMIT ?",
                    (project_path, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM reflections ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            return [self._row_to_reflection(row) for row in cur.fetchall()]

    def recall(
        self,
        query: str,
        *,
        top_k: int = DEFAULT_RECALL_TOP_K,
        project_path: str | None = None,
        max_age_days: int = DEFAULT_RECALL_MAX_AGE_DAYS,
    ) -> RecallResult:
        """Ищет top-K релевантных рефлексий по FTS5 (BM25).

        Args:
            query: Текст запроса (что делает пользователь сейчас).
            top_k: Сколько записей вернуть.
            project_path: Ограничить проектом (если задан).
            max_age_days: Не старше N дней (0 — без ограничения).

        Returns:
            `RecallResult` с рефлексиями и флагом ``hit``.
        """
        q = (query or "").strip()
        if not q:
            return RecallResult(reflections=[], hit=False)

        # FTS5-запрос: разбиваем на токены, объединяем через OR, чтобы
        # не требовать точного совпадения всей фразы.
        tokens = [t for t in q.split() if len(t) >= 3][:8]
        if not tokens:
            return RecallResult(reflections=[], hit=False)
        fts_query = " OR ".join(f'"{t}"' for t in tokens)

        cutoff = ""
        if max_age_days > 0:
            cutoff = datetime.fromtimestamp(time.time() - max_age_days * 86400).isoformat(timespec="seconds")

        # Пробуем FTS5; при ошибке — LIKE-fallback.
        try:
            sql = (
                "SELECT r.* FROM reflections r "
                "JOIN reflections_fts f ON f.rowid = r.id "
                "WHERE reflections_fts MATCH ?"
            )
            params: list[Any] = [fts_query]
            if project_path:
                sql += " AND r.project_path = ?"
                params.append(project_path)
            if cutoff:
                sql += " AND r.created_at >= ?"
                params.append(cutoff)
            sql += " ORDER BY bm25(reflections_fts) LIMIT ?"
            params.append(top_k)

            with self._lock:
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall()
        except sqlite3.OperationalError as e:
            logger.debug(f"FTS5 recall упал ({e}); fallback на LIKE")
            like_pattern = f"%{q}%"
            sql = "SELECT * FROM reflections WHERE summary LIKE ?"
            params = [like_pattern]
            if project_path:
                sql += " AND project_path = ?"
                params.append(project_path)
            if cutoff:
                sql += " AND created_at >= ?"
                params.append(cutoff)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(top_k)
            with self._lock:
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall()

        reflections = [self._row_to_reflection(row) for row in rows]
        if reflections:
            emit_event(
                EventKind.MEMORY_RECALLED.value,
                {
                    "query": q,
                    "n_results": len(reflections),
                    "ids": [r.id for r in reflections if r.id is not None],
                },
            )
        return RecallResult(reflections=reflections, hit=bool(reflections))

    @staticmethod
    def _row_to_reflection(row: sqlite3.Row) -> Reflection:
        try:
            tags = json.loads(row["tags"] or "[]")
            if not isinstance(tags, list):
                tags = []
        except (json.JSONDecodeError, TypeError):
            tags = []
        return Reflection(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            project_path=str(row["project_path"]),
            task_type=str(row["task_type"]),  # type: ignore[arg-type]
            summary=str(row["summary"]),
            outcome=str(row["outcome"]),  # type: ignore[arg-type]
            tags=[str(t) for t in tags],
        )

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def remember_decision(self, decision: Decision) -> int:
        """Сохраняет архитектурное решение."""
        text = self._truncate(decision.decision, MAX_RATIONALE_CHARS)
        if not text:
            raise ValueError("decision.decision пуст")
        rationale = self._truncate(decision.rationale, MAX_RATIONALE_CHARS)
        created_at = decision.created_at or self._now()
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO decisions (created_at, project_path, decision, rationale) VALUES (?, ?, ?, ?)",
                (
                    created_at,
                    decision.project_path,
                    text,
                    rationale,
                ),
            )
            return int(cur.lastrowid or 0)

    def list_decisions(
        self,
        limit: int = 50,
        *,
        project_path: str | None = None,
    ) -> list[Decision]:
        with self._lock:
            if project_path:
                cur = self._conn.execute(
                    "SELECT * FROM decisions WHERE project_path = ? ORDER BY id DESC LIMIT ?",
                    (project_path, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM decisions ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            return [
                Decision(
                    id=int(r["id"]),
                    created_at=str(r["created_at"]),
                    project_path=str(r["project_path"]),
                    decision=str(r["decision"]),
                    rationale=str(r["rationale"]),
                )
                for r in cur.fetchall()
            ]

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    def set_preference(self, key: str, value: str) -> None:
        """Устанавливает пользовательскую настройку."""
        k = (key or "").strip()
        if not k:
            raise ValueError("preference.key пуст")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO preferences(key, value, updated_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET"
                " value = excluded.value, updated_at = excluded.updated_at",
                (k, str(value), self._now()),
            )

    def get_preference(self, key: str, default: str | None = None) -> str | None:
        """Читает настройку; ``default``, если нет."""
        with self._lock:
            cur = self._conn.execute("SELECT value FROM preferences WHERE key = ?", (key,))
            row = cur.fetchone()
        return str(row["value"]) if row else default

    def list_preferences(self) -> list[Preference]:
        with self._lock:
            cur = self._conn.execute("SELECT key, value, updated_at FROM preferences ORDER BY key")
            return [
                Preference(
                    key=str(r["key"]),
                    value=str(r["value"]),
                    updated_at=str(r["updated_at"]),
                )
                for r in cur.fetchall()
            ]

    # ------------------------------------------------------------------
    # Очистка / экспорт
    # ------------------------------------------------------------------

    def clear(self, *, keep_preferences: bool = True) -> None:
        """Удаляет все рефлексии и решения.

        Args:
            keep_preferences: ``True`` — настройки пользователя сохраняются
                (это «настройки», а не «память»).
        """
        with self._tx() as conn:
            conn.execute("DELETE FROM reflections")
            conn.execute("DELETE FROM decisions")
            if not keep_preferences:
                conn.execute("DELETE FROM preferences")
        logger.info(f"Memory: clear (keep_preferences={keep_preferences})")

    def count(self) -> dict[str, int]:
        """Счётчики по таблицам (для UI)."""
        with self._lock:
            refl = self._conn.execute("SELECT COUNT(*) AS c FROM reflections").fetchone()
            dec = self._conn.execute("SELECT COUNT(*) AS c FROM decisions").fetchone()
            pref = self._conn.execute("SELECT COUNT(*) AS c FROM preferences").fetchone()
        return {
            "reflections": int(refl["c"]) if refl else 0,
            "decisions": int(dec["c"]) if dec else 0,
            "preferences": int(pref["c"]) if pref else 0,
        }

    def export_json(self, path: Path) -> None:
        """Экспортирует всё в JSON (для бэкапа/аудита)."""
        data = {
            "exported_at": self._now(),
            "reflections": [
                {
                    "id": r.id,
                    "created_at": r.created_at,
                    "project_path": r.project_path,
                    "task_type": r.task_type,
                    "summary": r.summary,
                    "outcome": r.outcome,
                    "tags": r.tags,
                }
                for r in self.list_recent(limit=10_000)
            ],
            "decisions": [
                {
                    "id": d.id,
                    "created_at": d.created_at,
                    "project_path": d.project_path,
                    "decision": d.decision,
                    "rationale": d.rationale,
                }
                for d in self.list_decisions(limit=10_000)
            ],
            "preferences": [
                {"key": p.key, "value": p.value, "updated_at": p.updated_at} for p in self.list_preferences()
            ],
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"Memory: exported {path}")

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Закрывает соединение (идемпотентно)."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                logger.debug("MemoryService.close: ошибка", exc_info=True)


__all__ = [
    "Decision",
    "MemoryService",
    "Preference",
    "RecallResult",
    "Reflection",
]
