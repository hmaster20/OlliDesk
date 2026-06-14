from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fs.indexer import TextChunk
from fs.vector_store import SearchResult, VectorStore


@pytest.fixture
def embed_client():
    client = MagicMock()
    client.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    return client


@pytest.fixture
def vector_store(tmp_path, embed_client):
    return VectorStore(
        persist_dir=tmp_path / "vector_db",
        embed_client=embed_client,
        embed_model="nomic-embed-text",
    )


class TestVectorStore:
    def test_init_creates_collection(self, tmp_path, embed_client):
        persist_dir = tmp_path / "vector_db"
        store = VectorStore(
            persist_dir=persist_dir,
            embed_client=embed_client,
            embed_model="test",
        )

        assert store.persist_dir == persist_dir
        assert store.embed_model == "test"
        assert store.collection.name == "codebase"

    @pytest.mark.asyncio
    async def test_add_chunks_empty(self, vector_store):
        await vector_store.add_chunks([])

    @pytest.mark.asyncio
    async def test_add_chunks_single(self, vector_store):
        chunk = TextChunk(
            id="chunk_1",
            file_path="main.py",
            content="print('hello')",
            start_line=1,
            end_line=1,
        )

        await vector_store.add_chunks([chunk])

        assert vector_store.collection.count() == 1

    @pytest.mark.asyncio
    async def test_add_chunks_multiple(self, vector_store):
        chunks = [
            TextChunk(
                id=f"chunk_{i}", file_path=f"file{i}.py",
                content=f"content{i}", start_line=1, end_line=1,
            )
            for i in range(5)
        ]

        with patch.object(
            vector_store.embed_client, "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3]] * 5),
        ):
            await vector_store.add_chunks(chunks)

        assert vector_store.collection.count() == 5

    @pytest.mark.asyncio
    async def test_search_returns_results(self, vector_store):
        chunk = TextChunk(
            id="chunk_1",
            file_path="main.py",
            content="def hello():\n    print('world')",
            start_line=1,
            end_line=2,
        )

        with patch.object(
            vector_store.embed_client, "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
        ):
            await vector_store.add_chunks([chunk])

            results = await vector_store.search("hello world", n_results=5)

        assert len(results) >= 1
        assert results[0].file_path == "main.py"
        assert results[0].chunk.content == "def hello():\n    print('world')"
        assert 0 <= results[0].score <= 1

    @pytest.mark.asyncio
    async def test_search_with_file_filter(self, vector_store):
        chunks = [
            TextChunk(id="c1", file_path="main.py", content="main code", start_line=1, end_line=1),
            TextChunk(
                id="c2", file_path="utils.py", content="utility code",
                start_line=1, end_line=1,
            ),
        ]

        with patch.object(
            vector_store.embed_client, "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]),
        ):
            await vector_store.add_chunks(chunks)

            embed_for_search = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
            with patch.object(vector_store.embed_client, "embed", embed_for_search):
                results = await vector_store.search(
                    "code", n_results=5, file_filter=["main.py"]
                )

        assert len(results) >= 1
        for r in results:
            assert r.file_path == "main.py"

    @pytest.mark.asyncio
    async def test_delete_by_file(self, vector_store):
        chunk = TextChunk(
            id="chunk_del",
            file_path="todelete.py",
            content="delete me",
            start_line=1,
            end_line=1,
        )

        with patch.object(
            vector_store.embed_client, "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
        ):
            await vector_store.add_chunks([chunk])

        assert vector_store.collection.count() == 1

        await vector_store.delete_by_file(["todelete.py"])

        assert vector_store.collection.count() == 0

    @pytest.mark.asyncio
    async def test_delete_by_file_empty(self, vector_store):
        await vector_store.delete_by_file([])

    @pytest.mark.asyncio
    async def test_clear(self, vector_store):
        chunk = TextChunk(
            id="chunk_clear",
            file_path="test.py",
            content="test",
            start_line=1,
            end_line=1,
        )

        with patch.object(
            vector_store.embed_client, "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
        ):
            await vector_store.add_chunks([chunk])

        assert vector_store.collection.count() == 1

        await vector_store.clear()

        assert vector_store.collection.count() == 0

    @pytest.mark.asyncio
    async def test_search_no_results(self, vector_store):
        with patch.object(
            vector_store.embed_client, "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
        ):
            results = await vector_store.search("something", n_results=5)

        assert len(results) == 0

    def test_search_result_model(self):
        chunk = TextChunk(
            id="test", file_path="f.py", content="code",
            start_line=1, end_line=2,
        )
        result = SearchResult(chunk=chunk, score=0.95, file_path="f.py")
        assert result.score == 0.95
        assert result.file_path == "f.py"
        assert result.chunk.content == "code"
