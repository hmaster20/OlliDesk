"""Тесты реранкера RAG (fs/reranker.py).

Модель-кросс-энкодер (torch) в тестах не грузится: `_load` подменяется
фейком с методом `predict`.
"""

from unittest.mock import patch

import pytest

from fs.indexer import TextChunk
from fs.reranker import CrossEncoderReranker
from fs.vector_store import SearchResult


def _result(chunk_id: str) -> SearchResult:
    chunk = TextChunk(
        id=chunk_id,
        file_path=f"{chunk_id}.py",
        content=f"content {chunk_id}",
        start_line=1,
        end_line=1,
    )
    return SearchResult(chunk=chunk, score=0.0, file_path=chunk.file_path)


class _FakeModel:
    def __init__(self, scores: list[float]):
        self.scores = scores

    def predict(self, pairs):
        return self.scores


class TestCrossEncoderReranker:
    @pytest.mark.asyncio
    async def test_rerank_sorts_by_score_desc(self):
        reranker = CrossEncoderReranker("fake-model")
        results = [_result("a"), _result("b"), _result("c")]

        with patch.object(reranker, "_load", return_value=_FakeModel([0.1, 0.9, 0.5])):
            out = await reranker.rerank("query", results)

        assert [r.chunk.id for r in out] == ["b", "c", "a"]

    @pytest.mark.asyncio
    async def test_single_result_skips_model(self):
        reranker = CrossEncoderReranker("fake-model")
        results = [_result("only")]

        with patch.object(reranker, "_load") as load:
            out = await reranker.rerank("query", results)

        assert out == results
        load.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_results(self):
        reranker = CrossEncoderReranker("fake-model")

        assert await reranker.rerank("query", []) == []
