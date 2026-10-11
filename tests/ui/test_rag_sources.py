"""Тесты кликабельных источников RAG (форматирование + поток поиска)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from fs.indexer import TextChunk
from fs.vector_store import SearchResult
from ui.chat.message_item import format_source_label
from ui.chat.threads import RagSearchThread


def _result(chunk_id: str, file_path: str, start: int, end: int) -> SearchResult:
    chunk = TextChunk(
        id=chunk_id,
        file_path=file_path,
        content=f"content of {file_path}",
        start_line=start,
        end_line=end,
    )
    return SearchResult(chunk=chunk, score=0.5, file_path=file_path)


class TestFormatSourceLabel:
    def test_single_line(self):
        assert format_source_label("src/a.py", 5, 5) == "src/a.py:5"

    def test_range(self):
        assert format_source_label("src/a.py", 5, 12) == "src/a.py:5-12"


class TestRagSearchThreadSources:
    """Поток отдаёт структурированные источники (файл + строки) без обрезки до 3."""

    def _run(self, results: list[SearchResult]) -> tuple[str, list]:
        store = MagicMock()
        store.search = AsyncMock(return_value=results)
        thread = RagSearchThread(store, "query", n_results=8)

        captured: dict = {}
        thread.results_ready.connect(lambda ctx, src: captured.update(ctx=ctx, src=src))
        asyncio.run(thread._search())
        return captured["ctx"], captured["src"]

    def test_emits_all_sources(self):
        results = [_result(f"c{i}", f"file{i}.py", i + 1, i + 3) for i in range(5)]

        context, sources = self._run(results)

        assert "file0.py" in context
        assert sources == [(f"file{i}.py", i + 1, i + 3) for i in range(5)]

    def test_dedupes_by_file(self):
        results = [
            _result("c1", "same.py", 1, 2),
            _result("c2", "same.py", 10, 12),
            _result("c3", "other.py", 5, 6),
        ]

        _context, sources = self._run(results)

        assert sources == [("same.py", 1, 2), ("other.py", 5, 6)]

    def test_empty_results_emit_empty(self):
        context, sources = self._run([])

        assert context == ""
        assert sources == []
