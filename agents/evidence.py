"""EvidenceStore: компактные, цитируемые, переиспользуемые доказательства.

Принцип: результаты инструментов НЕ живут в истории диалога. В промпт 7B
попадает пакет вида «[E1] Program.cs:1-40 …», а полный текст хранится здесь.
Это даёт: меньше токенов, нулевые галлюцинации путей, проверяемость (verifier),
кэш резюме модулей между итерациями/сессиями.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

EvidenceKind = Literal[
    "file_excerpt", "symbol", "project_map", "dependency_edge",
    "module_summary", "manifest", "entry_point", "tool_result",
    "vector_chunk", "git_diff", "doc_excerpt",
]


@dataclass
class EvidenceItem:
    id: str
    kind: EvidenceKind
    content: str
    source_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    summary: str | None = None
    tokens: int = 0
    relevance: float = 1.0
    iteration: int = 0
    hash: str = ""

    def __post_init__(self) -> None:
        if not self.hash:
            self.hash = hashlib.sha256(
                f"{self.kind}|{self.source_path}|{self.content}".encode()
            ).hexdigest()[:16]


class EvidenceStore:
    """In-memory + SQLite-персист для переиспользования между сессиями."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._items: dict[str, EvidenceItem] = {}
        self._order: list[str] = []
        self._by_hash: dict[str, str] = {}
        self._db = db_path
        self._conn: sqlite3.Connection | None = None
        if db_path:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS evidence("
                " id TEXT PRIMARY KEY, kind TEXT, source_path TEXT,"
                " start_line INT, end_line INT, summary TEXT, content TEXT,"
                " tokens INT, relevance REAL, hash TEXT)"
            )
            self._conn.commit()

    def put(self, item: EvidenceItem) -> str:
        """Добавить (идемпотентно по hash). Возвращает id."""
        if item.hash in self._by_hash:
            return self._by_hash[item.hash]
        if not item.id:
            item.id = f"E{len(self._order)+1}"
        self._items[item.id] = item
        self._order.append(item.id)
        self._by_hash[item.hash] = item.id
        if self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                (item.id, item.kind, item.source_path, item.start_line,
                 item.end_line, item.summary, item.content, item.tokens,
                 item.relevance, item.hash),
            )
            self._conn.commit()
        return item.id

    def get_module_summary(self, path: str) -> EvidenceItem | None:
        for iid in self._order:
            it = self._items[iid]
            if it.kind == "module_summary" and it.source_path == path:
                return it
        return None

    def pack_for_prompt(self, budget_tokens: int, counter) -> str:
        """Собрать компактный пакет доказательств в рамках бюджета.

        Порядок: entry_point → manifest → project_map → dependency_edge →
        symbol → module_summary → file_excerpt → vector_chunk → doc/git.
        Каждый элемент форматируется как «[Ei] источник:…\nсодержимое».
        """
        prio: list[EvidenceKind] = [
            "entry_point", "manifest", "project_map", "dependency_edge",
            "symbol", "module_summary", "file_excerpt", "vector_chunk",
            "doc_excerpt", "git_diff", "tool_result",
        ]
        rank = {k: i for i, k in enumerate(prio)}
        ordered = sorted(
            (self._items[i] for i in self._order),
            key=lambda e: (rank.get(e.kind, 99), -e.relevance),
        )
        parts: list[str] = []
        used = 0
        for e in ordered:
            head = f"[{e.id}] {e.kind}"
            if e.source_path:
                loc = f"{e.source_path}"
                if e.start_line is not None:
                    loc += f":{e.start_line}"
                    if e.end_line is not None and e.end_line != e.start_line:
                        loc += f"-{e.end_line}"
                head += f" {loc}"
            body = e.summary or e.content
            block = f"{head}\n{body}"
            cost = counter.count(block)
            if used + cost > budget_tokens:
                break
            parts.append(block)
            used += cost
        return "\n\n".join(parts)

    def ids_referenced(self, text: str) -> set[str]:
        import re
        return set(re.findall(r"\[(E\d+)\]", text))

    def all_paths(self) -> set[str]:
        return {e.source_path for e in self._items.values() if e.source_path}