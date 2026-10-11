from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.exceptions import EmbedderError
from fs.indexer import TextChunk
from fs.vector_store import Embedder, SearchResult, VectorStore, category_for_language


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
                id=f"chunk_{i}",
                file_path=f"file{i}.py",
                content=f"content{i}",
                start_line=1,
                end_line=1,
            )
            for i in range(5)
        ]

        with patch.object(
            vector_store.embed_client,
            "embed",
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
            vector_store.embed_client,
            "embed",
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
                id="c2",
                file_path="utils.py",
                content="utility code",
                start_line=1,
                end_line=1,
            ),
        ]

        with patch.object(
            vector_store.embed_client,
            "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]),
        ):
            await vector_store.add_chunks(chunks)

            embed_for_search = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
            with patch.object(vector_store.embed_client, "embed", embed_for_search):
                results = await vector_store.search("code", n_results=5, file_filter=["main.py"])

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
            vector_store.embed_client,
            "embed",
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
            vector_store.embed_client,
            "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
        ):
            await vector_store.add_chunks([chunk])

        assert vector_store.collection.count() == 1

        await vector_store.clear()

        assert vector_store.collection.count() == 0

    @pytest.mark.asyncio
    async def test_search_no_results(self, vector_store):
        with patch.object(
            vector_store.embed_client,
            "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
        ):
            results = await vector_store.search("something", n_results=5)

        assert len(results) == 0

    def test_search_result_model(self):
        chunk = TextChunk(
            id="test",
            file_path="f.py",
            content="code",
            start_line=1,
            end_line=2,
        )
        result = SearchResult(chunk=chunk, score=0.95, file_path="f.py")
        assert result.score == 0.95
        assert result.file_path == "f.py"
        assert result.chunk.content == "code"


class TestVectorStoreEmbedder:
    """Проверки того, что VectorStore больше не завязан на OllamaClient."""

    def test_rejects_object_without_embed(self, tmp_path):
        with pytest.raises(EmbedderError, match="не умеет считать эмбеддинги"):
            VectorStore(
                persist_dir=tmp_path / "vector_db",
                embed_client=cast(Embedder, object()),
                embed_model="nomic-embed-text",
            )

    def test_accepts_any_object_with_embed(self, tmp_path):
        class FakeLocalClient:
            async def embed(self, model, texts):
                return [[0.1, 0.2, 0.3] for _ in texts]

        store = VectorStore(
            persist_dir=tmp_path / "vector_db",
            embed_client=cast(Embedder, FakeLocalClient()),
            embed_model="nomic-embed-text",
        )
        assert store.embed_model == "nomic-embed-text"

    @pytest.mark.asyncio
    async def test_add_chunks_raises_on_empty_embeddings(self, vector_store):
        chunk = TextChunk(
            id="c1",
            file_path="main.py",
            content="code",
            start_line=1,
            end_line=1,
        )
        vector_store.embed_client.embed = AsyncMock(return_value=[])

        with pytest.raises(EmbedderError, match="пустые эмбеддинги"):
            await vector_store.add_chunks([chunk])

        assert vector_store.collection.count() == 0

    @pytest.mark.asyncio
    async def test_add_chunks_raises_on_count_mismatch(self, vector_store):
        chunks = [
            TextChunk(
                id=f"c{i}",
                file_path="main.py",
                content=f"code{i}",
                start_line=1,
                end_line=1,
            )
            for i in range(3)
        ]
        vector_store.embed_client.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

        with pytest.raises(EmbedderError, match="не совпадают"):
            await vector_store.add_chunks(chunks)

    @pytest.mark.asyncio
    async def test_add_chunks_wraps_backend_error(self, vector_store):
        chunk = TextChunk(
            id="c1",
            file_path="main.py",
            content="code",
            start_line=1,
            end_line=1,
        )
        vector_store.embed_client.embed = AsyncMock(side_effect=ConnectionError("нет сети"))

        with pytest.raises(EmbedderError, match="нет сети"):
            await vector_store.add_chunks([chunk])

    @pytest.mark.asyncio
    async def test_search_degrades_gracefully(self, vector_store):
        """Поиск не должен ронять чат, если эмбеддинги недоступны."""
        vector_store.embed_client.embed = AsyncMock(return_value=[])

        assert await vector_store.search("hello", n_results=5) == []


class TestHybridSearch:
    """Гибридный поиск: BM25 страхует векторную часть и объединяется через RRF."""

    @pytest.mark.asyncio
    async def test_bm25_rescues_when_embeddings_unavailable(self, vector_store):
        chunk = TextChunk(
            id="c1",
            file_path="a.py",
            content="def unique_function(): pass",
            start_line=1,
            end_line=1,
        )
        with patch.object(vector_store.embed_client, "embed", AsyncMock(return_value=[[0.1, 0.2, 0.3]])):
            await vector_store.add_chunks([chunk])

        # Эмбеддер пропал — векторная часть пустая, но BM25 находит по токенам.
        vector_store.embed_client.embed = AsyncMock(return_value=[])

        results = await vector_store.search("unique_function", n_results=5)

        assert len(results) == 1
        assert results[0].file_path == "a.py"

    @pytest.mark.asyncio
    async def test_chunk_found_by_both_ranked_once(self, vector_store):
        chunk = TextChunk(
            id="c1",
            file_path="a.py",
            content="def hello(): return 1",
            start_line=1,
            end_line=1,
        )
        with patch.object(vector_store.embed_client, "embed", AsyncMock(return_value=[[0.1, 0.2, 0.3]])):
            await vector_store.add_chunks([chunk])

        with patch.object(vector_store.embed_client, "embed", AsyncMock(return_value=[[0.1, 0.2, 0.3]])):
            results = await vector_store.search("hello", n_results=10)

        ids = [r.chunk.id for r in results]
        assert ids == ["c1"]

    @pytest.mark.asyncio
    async def test_bm25_respects_file_filter(self, vector_store):
        chunks = [
            TextChunk(id="c1", file_path="main.py", content="token alpha", start_line=1, end_line=1),
            TextChunk(id="c2", file_path="utils.py", content="token alpha", start_line=1, end_line=1),
        ]
        with patch.object(
            vector_store.embed_client,
            "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]),
        ):
            await vector_store.add_chunks(chunks)

        # Эмбеддер недоступен, поэтому работает только BM25 — и он тоже фильтрует.
        vector_store.embed_client.embed = AsyncMock(return_value=[])

        results = await vector_store.search("alpha", n_results=5, file_filter=["utils.py"])

        assert results
        assert all(r.file_path == "utils.py" for r in results)

    @pytest.mark.asyncio
    async def test_bm25_cache_invalidated_on_add(self, vector_store):
        with patch.object(vector_store.embed_client, "embed", AsyncMock(return_value=[[0.1, 0.2, 0.3]])):
            await vector_store.add_chunks(
                [TextChunk(id="c1", file_path="a.py", content="alpha", start_line=1, end_line=1)]
            )
            await vector_store.search("alpha", n_results=5)

            # После добавления нового чанка кэш BM25 должен пересобраться.
            await vector_store.add_chunks(
                [TextChunk(id="c2", file_path="b.py", content="beta", start_line=1, end_line=1)]
            )

        vector_store.embed_client.embed = AsyncMock(return_value=[])
        results = await vector_store.search("beta", n_results=5)

        assert [r.file_path for r in results] == ["b.py"]


class TestSearchFilters:
    """Фильтры поиска по языку и префиксу пути."""

    async def _seed(self, vector_store, chunks):
        embeddings = [[0.1, 0.2, 0.3]] * len(chunks)
        with patch.object(vector_store.embed_client, "embed", AsyncMock(return_value=embeddings)):
            await vector_store.add_chunks(chunks)

    @staticmethod
    def _chunks() -> list[TextChunk]:
        return [
            TextChunk(
                id="py",
                file_path="agents/loop.py",
                content="token alpha",
                start_line=1,
                end_line=1,
                metadata={"language": "python"},
            ),
            TextChunk(
                id="js",
                file_path="ui/panel.js",
                content="token alpha",
                start_line=1,
                end_line=1,
                metadata={"language": "javascript"},
            ),
        ]

    @pytest.mark.asyncio
    async def test_language_filter_bm25(self, vector_store):
        await self._seed(vector_store, self._chunks())
        vector_store.embed_client.embed = AsyncMock(return_value=[])

        results = await vector_store.search("alpha", n_results=5, language="python")

        assert [r.file_path for r in results] == ["agents/loop.py"]

    @pytest.mark.asyncio
    async def test_language_filter_vector(self, vector_store):
        await self._seed(vector_store, self._chunks())

        with patch.object(vector_store.embed_client, "embed", AsyncMock(return_value=[[0.1, 0.2, 0.3]])):
            results = await vector_store.search("alpha", n_results=5, language="javascript")

        assert results
        assert all(r.file_path == "ui/panel.js" for r in results)

    @pytest.mark.asyncio
    async def test_path_prefix_filter_bm25(self, vector_store):
        await self._seed(vector_store, self._chunks())
        vector_store.embed_client.embed = AsyncMock(return_value=[])

        results = await vector_store.search("alpha", n_results=5, path_prefix="agents")

        assert [r.file_path for r in results] == ["agents/loop.py"]

    @pytest.mark.asyncio
    async def test_combined_filters(self, vector_store):
        await self._seed(vector_store, self._chunks())
        vector_store.embed_client.embed = AsyncMock(return_value=[])

        results = await vector_store.search("alpha", n_results=5, language="python", path_prefix="ui")

        assert results == []


class _ReversingReranker:
    """Тестовый реранкер: просто переворачивает порядок."""

    async def rerank(self, query, results):
        return list(reversed(results))


class _BoomReranker:
    async def rerank(self, query, results):
        raise RuntimeError("reranker unavailable")


class TestRerankerIntegration:
    """`VectorStore.search` применяет реранкер после RRF и терпит его падение."""

    async def _seed_two(self, vector_store):
        chunks = [
            TextChunk(id="c1", file_path="a.py", content="token alpha", start_line=1, end_line=1),
            TextChunk(id="c2", file_path="b.py", content="token alpha", start_line=1, end_line=1),
        ]
        with patch.object(
            vector_store.embed_client,
            "embed",
            AsyncMock(return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]),
        ):
            await vector_store.add_chunks(chunks)
        # Только BM25, чтобы порядок до реранка был детерминированным.
        vector_store.embed_client.embed = AsyncMock(return_value=[])

    @pytest.mark.asyncio
    async def test_reranker_reorders(self, vector_store):
        await self._seed_two(vector_store)
        vector_store.reranker = _ReversingReranker()

        results = await vector_store.search("alpha", n_results=5)

        assert [r.chunk.id for r in results] == ["c2", "c1"]

    @pytest.mark.asyncio
    async def test_reranker_failure_falls_back(self, vector_store):
        await self._seed_two(vector_store)
        vector_store.reranker = _BoomReranker()

        results = await vector_store.search("alpha", n_results=5)

        assert [r.chunk.id for r in results] == ["c1", "c2"]

    @pytest.mark.asyncio
    async def test_rerank_pool_limits_candidates(self, vector_store):
        await self._seed_two(vector_store)
        captured: dict = {}

        class _CapturingReranker:
            async def rerank(self, query, results):
                captured["n"] = len(results)
                return results

        vector_store.reranker = _CapturingReranker()
        vector_store.rerank_pool = 1

        await vector_store.search("alpha", n_results=5)

        assert captured["n"] == 1


class TestCategoryForLanguage:
    def test_docs_languages(self):
        assert category_for_language("markdown") == "docs"
        assert category_for_language("rst") == "docs"

    def test_code_languages(self):
        assert category_for_language("python") == "code"
        assert category_for_language("unknown") == "code"


@pytest.fixture
def split_client():
    client = MagicMock()
    client.embed = AsyncMock(side_effect=lambda model, texts: [[0.1, 0.2, 0.3] for _ in texts])
    return client


@pytest.fixture
def split_store(tmp_path, split_client):
    return VectorStore(
        persist_dir=tmp_path / "vector_db",
        embed_client=split_client,
        embed_model="default-model",
        embed_models={"docs": "doc-model"},
    )


class TestEmbeddingSplit:
    """Чанки расходятся по коллекциям моделей согласно категории файла."""

    def test_without_split_single_collection(self, tmp_path, embed_client):
        store = VectorStore(
            persist_dir=tmp_path / "vector_db",
            embed_client=embed_client,
            embed_model="only",
        )
        assert store.embed_models == {"code": "only", "docs": "only"}
        assert list(store._collections) == ["only"]

    @pytest.mark.asyncio
    async def test_chunks_routed_by_category(self, split_store, split_client):
        chunks = [
            TextChunk(
                id="py",
                file_path="a.py",
                content="code",
                start_line=1,
                end_line=1,
                metadata={"language": "python"},
            ),
            TextChunk(
                id="md",
                file_path="a.md",
                content="doc",
                start_line=1,
                end_line=1,
                metadata={"language": "markdown"},
            ),
        ]

        await split_store.add_chunks(chunks)

        assert split_store.collection.count() == 1
        assert split_store._get_collection("doc-model").count() == 1
        used = {call.args[0] for call in split_client.embed.call_args_list}
        assert used == {"default-model", "doc-model"}

    @pytest.mark.asyncio
    async def test_search_spans_collections(self, split_store):
        chunks = [
            TextChunk(
                id="py",
                file_path="a.py",
                content="alpha code",
                start_line=1,
                end_line=1,
                metadata={"language": "python"},
            ),
            TextChunk(
                id="md",
                file_path="a.md",
                content="alpha doc",
                start_line=1,
                end_line=1,
                metadata={"language": "markdown"},
            ),
        ]
        await split_store.add_chunks(chunks)

        results = await split_store.search("alpha", n_results=5)

        assert {r.file_path for r in results} == {"a.py", "a.md"}

    @pytest.mark.asyncio
    async def test_delete_by_file_across_collections(self, split_store):
        chunks = [
            TextChunk(
                id="py",
                file_path="a.py",
                content="code",
                start_line=1,
                end_line=1,
                metadata={"language": "python"},
            ),
            TextChunk(
                id="md",
                file_path="a.md",
                content="doc",
                start_line=1,
                end_line=1,
                metadata={"language": "markdown"},
            ),
        ]
        await split_store.add_chunks(chunks)

        await split_store.delete_by_file(["a.md"])

        assert split_store._get_collection("doc-model").count() == 0
        assert split_store.collection.count() == 1

    @pytest.mark.asyncio
    async def test_clear_removes_all_collections(self, split_store):
        chunk = TextChunk(
            id="md",
            file_path="a.md",
            content="doc",
            start_line=1,
            end_line=1,
            metadata={"language": "markdown"},
        )
        await split_store.add_chunks([chunk])

        await split_store.clear()

        assert split_store.collection.count() == 0
        assert split_store._get_collection("doc-model").count() == 0
