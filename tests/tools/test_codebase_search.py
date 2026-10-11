"""Тесты инструмента поиска по коду `search_codebase`."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fs.indexer import TextChunk
from fs.vector_store import SearchResult
from tools.codebase_search import search_codebase


def _result(file_path: str) -> SearchResult:
    chunk = TextChunk(id="c", file_path=file_path, content="code", start_line=1, end_line=2)
    return SearchResult(chunk=chunk, score=0.9, file_path=file_path)


def _store(results: list[SearchResult]) -> MagicMock:
    store = MagicMock()
    store.search = AsyncMock(return_value=results)
    return store


class TestSearchCodebase:
    @pytest.mark.asyncio
    async def test_without_store(self):
        assert "не подключено" in await search_codebase("q")

    @pytest.mark.asyncio
    async def test_passes_filters(self):
        store = _store([_result("agents/loop.py")])

        await search_codebase("q", n_results=3, language="python", path="agents", vector_store=store)

        store.search.assert_awaited_once_with("q", n_results=3, language="python", path_prefix="agents")

    @pytest.mark.asyncio
    async def test_defaults_map_to_none(self):
        store = _store([])

        out = await search_codebase("q", vector_store=store)

        store.search.assert_awaited_once_with("q", n_results=5, language=None, path_prefix=None)
        assert "Ничего не найдено" in out

    @pytest.mark.asyncio
    async def test_clamps_n_results(self):
        store = _store([])

        await search_codebase("q", n_results=99, vector_store=store)

        assert store.search.await_args.kwargs["n_results"] == 10

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self):
        store = _store([_result("a.py")])

        out = await search_codebase("q", vector_store=store)

        assert "a.py" in out
        assert "строки 1-2" in out

    @pytest.mark.asyncio
    async def test_returns_error_string_on_exception(self):
        store = MagicMock()
        store.search = AsyncMock(side_effect=RuntimeError("boom"))

        out = await search_codebase("q", vector_store=store)

        assert out.startswith("Ошибка поиска:")
