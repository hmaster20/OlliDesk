"""Protocol-контракты для сервисов, инжектируемых в `AgentContext`.

Зачем отдельный модуль: `agents.agent_loop` не должен импортировать `fs` и
`tools` — это создаёт цикл `agents → fs → core` / `agents → tools → agents`.
Раньше `AgentContext.vector_store` / `.search_cache` / `.attached_files` /
`.hypotheses` были `object = None`, и `AgentLoop` работал с ними через
`hasattr(...)` — источник неочевидных `None`-ошибок в рантайме.

Здесь — минимальные протоколы **только на тот API**, который реально
использует `AgentLoop` (плюс инструменты через `extra`). Конкретные
реализации (`fs.vector_store.VectorStore`, `core.search_cache.SearchCache`,
`tools.attached_files.AttachedFileStore`, `agents.hypotheses.HypothesisStore`)
структурно им удовлетворяют — проверяется mypy, без явного наследования.

Инварианты:
  * Протоколы НЕ `runtime_checkable`: проверки через `isinstance` не нужны,
    `if ctx.vector_store:` работает через `__bool__` конкретного класса.
  * `Sequence` вместо `list` в возвращаемых типах — `list[SearchResult]`
    ковариантно совместим с `Sequence[SearchResultLike]` (mypy требование:
    `list` инвариантен).
  * `HypothesisStore` сюда **не** входит — он живёт в `agents.hypotheses`,
    импортируется в `agent_loop` напрямую (цикла нет).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

# ---------------------------------------------------------------------------
# RAG: VectorStoreLike
# ---------------------------------------------------------------------------


class ChunkLike(Protocol):
    """Минимальный контракт чанка результата поиска.

    Полный `TextChunk` (fs.indexer) — pydantic-модель с этими полями
    плюс `id` и `metadata`; для `search_codebase` нужны только эти 4.
    """

    file_path: str
    content: str
    start_line: int
    end_line: int


class SearchResultLike(Protocol):
    """Один результат RAG-поиска (fs.vector_store.SearchResult).

    Используется в `tools/codebase_search.py` для форматирования выдачи
    и в `Orchestrator` для сборки evidence-пакета.
    """

    chunk: ChunkLike
    score: float
    file_path: str


class VectorStoreLike(Protocol):
    """Минимальный контракт векторного хранилища для `AgentContext`.

    `AgentLoop` сам методы не вызывает — прокидывает объект в инструменты
    через `extra["vector_store"]`. Инструмент `search_codebase` использует
    только `search(...)`.
    """

    async def search(
        self,
        query: str,
        n_results: int = 10,
        file_filter: list[str] | None = None,
        language: str | None = None,
        path_prefix: str | None = None,
        modified_after: float | None = None,
        modified_before: float | None = None,
    ) -> Sequence[SearchResultLike]: ...


# ---------------------------------------------------------------------------
# Web search: SearchCacheLike
# ---------------------------------------------------------------------------


class SearchCacheLike(Protocol):
    """Минимальный контракт кэша поисковых запросов.

    `web_search` использует `get`/`set`; расширенный API (`get_list`/
    `set_list` для реранкера) живёт в `core.search_cache.SearchCache`,
    но `AgentContext.search_cache` типизирован именно этим минимумом —
    чтобы не тянуть `SearchCache` в `agents`.
    """

    def get(self, query: str, max_age_hours: int = 24) -> str | None: ...

    def set(self, query: str, result: str) -> None: ...


# ---------------------------------------------------------------------------
# Прикреплённые файлы: AttachedFilesLike
# ---------------------------------------------------------------------------


class AttachedFilesLike(Protocol):
    """Минимальный контракт хранилища `@file`-вложений.

    `AgentLoop` методы не вызывает — только прокидывает объект в
    `extra["attached_files"]` для инструмента `read_attached_file`.
    Поэтому контракт ограничен `__bool__` (нужен для `if ctx.attached_files:`).

    Полный API (`find`, `render`, `files`, `add`, `clear`) реализован в
    `tools.attached_files.AttachedFileStore`, но `agents` не может его
    импортировать: `tools.attached_files` импортирует `agents.tool_registry`
    — возник бы цикл `agents → tools → agents`.
    """

    def __bool__(self) -> bool: ...
