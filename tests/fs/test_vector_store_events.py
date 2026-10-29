"""Тесты 2.5: эмиты `rag.search.*` из `VectorStore.search`."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.event_bus import EventBus, subscribe
from core.events import EventKind
from fs.indexer import TextChunk
from fs.vector_store import VectorStore


class _FakeEmbedder:
    """Детерминированный эмбеддер без внешних зависимостей."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dim for _ in texts]


@pytest.fixture(autouse=True)
def _clean_bus():
    EventBus.instance().clear()
    yield
    EventBus.instance().clear()


@pytest.mark.asyncio
async def test_search_emits_rag_started(tmp_path: Path) -> None:
    events: list[dict] = []
    unsub = subscribe(EventKind.RAG_SEARCH_STARTED.value, lambda _k, p: events.append(p))
    try:
        store = VectorStore(
            persist_dir=tmp_path / "vs",
            embed_client=_FakeEmbedder(),
            embed_model="fake",
        )
        await store.search("hello", n_results=3)
    finally:
        unsub()

    assert events, "ожидалось rag.search.started"
    assert events[0]["query"] == "hello"
    assert events[0]["n_results"] == 3


@pytest.mark.asyncio
async def test_search_emits_rag_empty(tmp_path: Path) -> None:
    events: list[dict] = []
    unsub = subscribe(EventKind.RAG_SEARCH_EMPTY.value, lambda _k, p: events.append(p))
    try:
        store = VectorStore(
            persist_dir=tmp_path / "vs",
            embed_client=_FakeEmbedder(),
            embed_model="fake",
        )
        results = await store.search("zzz-not-found", n_results=3)
    finally:
        unsub()

    assert results == []
    assert events, "ожидалось rag.search.empty"
    assert events[0]["query"] == "zzz-not-found"
    assert events[0]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_search_emits_rag_found(tmp_path: Path) -> None:
    events: list[dict] = []
    unsub = subscribe(EventKind.RAG_SEARCH_FOUND.value, lambda _k, p: events.append(p))
    try:
        store = VectorStore(
            persist_dir=tmp_path / "vs",
            embed_client=_FakeEmbedder(),
            embed_model="fake",
        )
        await store.add_chunks(
            [
                TextChunk(
                    id="c1",
                    file_path="a.py",
                    content="hello world",
                    start_line=1,
                    end_line=1,
                    metadata={"language": "python"},
                ),
            ]
        )
        results = await store.search("hello", n_results=5)
    finally:
        unsub()

    assert results, "должен быть хотя бы один результат"
    assert events, "ожидалось rag.search.found"
    assert events[0]["query"] == "hello"
    assert events[0]["n_results"] >= 1