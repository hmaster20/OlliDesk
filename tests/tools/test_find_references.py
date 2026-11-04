"""10.3: find_references — LSP + fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tools.codebase_search import find_references


class TestFindReferences:
    @pytest.mark.asyncio
    async def test_empty_symbol(self) -> None:
        result = await find_references(symbol="")
        assert "пустое имя" in result

    @pytest.mark.asyncio
    async def test_no_vector_store(self) -> None:
        result = await find_references(symbol="foo")
        assert "RAG не подключён" in result

    @pytest.mark.asyncio
    async def test_fallback_rag(self) -> None:
        # Мок vector_store.
        result_mock = MagicMock()
        result_mock.file_path = "src/a.py"
        result_mock.chunk.content = "def foo():\n    pass\n"
        result_mock.chunk.start_line = 1
        result_mock.chunk.end_line = 2
        store = MagicMock()
        store.search = AsyncMock(return_value=[result_mock])

        result = await find_references(symbol="foo", vector_store=store)
        assert "Эвристический поиск" in result
        assert "src/a.py" in result