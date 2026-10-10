"""Векторное хранилище на основе ChromaDB."""

from pathlib import Path
from typing import Protocol, runtime_checkable

import chromadb
from chromadb.config import Settings
from loguru import logger
from pydantic import BaseModel

from core.exceptions import EmbedderError
from fs.bm25 import BM25Index
from fs.chroma_adapter import ChromaCollection, ChunkMetadata, where_file_paths
from fs.indexer import TextChunk


@runtime_checkable
class Embedder(Protocol):
    """Минимальный контракт объекта, умеющего считать эмбеддинги.

    Нужен, чтобы `VectorStore` не зависел от конкретного бэкенда: подойдёт
    и `OllamaClient`, и `LocalLLMClient`, и `ModelRouter`, который сам
    переключается между ними.
    """

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Возвращает по одному вектору на каждый текст из ``texts``."""
        ...


class SearchResult(BaseModel):
    """Результат поиска."""

    chunk: TextChunk
    score: float
    file_path: str


def _reciprocal_rank_fusion(*result_lists: list[SearchResult], k: int = 60) -> list[SearchResult]:
    """Объединяет выдачи разных поисковиков по reciprocal rank fusion.

    RRF не сравнивает «сырые» оценки (у косинуса и BM25 они несопоставимы),
    а складывает ``1/(k + rank)`` по позициям документа в каждой выдаче.
    Документ, найденный обоими поисковиками, получает вклад дважды.

    Args:
        *result_lists: Выдачи (например, векторная и BM25) в порядке приоритета.
        k: Сглаживание ранга; стандартное значение — 60.

    Returns:
        Объединённая выдача, отсортированная по убыванию RRF-оценки.
        В поле ``score`` кладётся RRF-оценка, а не исходная похожесть.
    """
    scores: dict[str, float] = {}
    by_id: dict[str, SearchResult] = {}
    for results in result_lists:
        for rank, result in enumerate(results):
            cid = result.chunk.id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            by_id.setdefault(cid, result)

    ordered = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [by_id[cid].model_copy(update={"score": scores[cid]}) for cid in ordered]


class VectorStore:
    """Векторное хранилище на основе ChromaDB."""

    def __init__(
        self,
        persist_dir: Path,
        embed_client: Embedder,
        embed_model: str,
    ):
        """
        Инициализация хранилища.

        Args:
            persist_dir: Папка для хранения ChromaDB
            embed_client: Объект с методом ``embed(model, texts)`` —
                `OllamaClient`, `LocalLLMClient` или `ModelRouter`
            embed_model: Имя модели для эмбеддингов

        Raises:
            EmbedderError: Если у ``embed_client`` нет метода ``embed``.
        """
        if not hasattr(embed_client, "embed"):
            raise EmbedderError(
                f"Объект {type(embed_client).__name__} не умеет считать эмбеддинги (нет метода embed)"
            )

        self.persist_dir = persist_dir
        self.embed_client = embed_client
        self.embed_model = embed_model

        # Инициализация ChromaDB
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )

        # Получение или создание коллекции
        self.collection = ChromaCollection(
            self.client.get_or_create_collection(
                name="codebase",
                metadata={"hnsw:space": "cosine"},
            )
        )

        # BM25-индекс строится лениво из всех чанков и сбрасывается при изменении
        # коллекции (см. `_invalidate_bm25`).
        self._bm25: BM25Index | None = None
        self._bm25_ids: list[str] = []
        self._bm25_docs: list[str] = []
        self._bm25_metas: list[ChunkMetadata] = []

        logger.info(f"VectorStore инициализирован: {self.persist_dir}")

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Считает эмбеддинги и проверяет, что их пришло ровно столько, сколько текстов.

        Args:
            texts: Тексты для векторизации

        Returns:
            Список векторов той же длины, что и ``texts``

        Raises:
            EmbedderError: Бэкенд вернул пустой результат, ошибку или
                неверное число векторов (индекс молча испортился бы).
        """
        try:
            vectors = await self.embed_client.embed(self.embed_model, texts)
        except Exception as e:
            raise EmbedderError(
                f"Не удалось посчитать эмбеддинги через {type(self.embed_client).__name__}: {e}"
            ) from e

        if not vectors:
            raise EmbedderError(
                f"{type(self.embed_client).__name__} вернул пустые эмбеддинги для "
                f"{len(texts)} текстов. Проверьте, что модель эмбеддингов "
                f"('{self.embed_model}') загружена и доступна."
            )
        if len(vectors) != len(texts):
            raise EmbedderError(
                f"Эмбеддингов {len(vectors)}, а текстов {len(texts)} — размерности не совпадают"
            )
        return vectors

    async def add_chunks(self, chunks: list[TextChunk]) -> None:
        """
        Добавляет чанки в хранилище.

        Args:
            chunks: Список чанков
        """
        if not chunks:
            return

        # Батчевая обработка
        batch_size = 32
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]

            # Векторизация
            texts = [chunk.content for chunk in batch]
            embeddings = await self._embed(texts)

            # Подготовка данных для ChromaDB
            ids = [chunk.id for chunk in batch]
            documents = [chunk.content for chunk in batch]
            metadatas: list[ChunkMetadata] = [
                {
                    "file_path": chunk.file_path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "language": str(chunk.metadata.get("language", "unknown")),
                }
                for chunk in batch
            ]

            # Добавление в коллекцию
            self.collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )

            logger.debug(f"Добавлено чанков: {len(batch)}")

        self._invalidate_bm25()
        logger.info(f"Всего добавлено чанков: {len(chunks)}")

    async def search(
        self,
        query: str,
        n_results: int = 10,
        file_filter: list[str] | None = None,
    ) -> list[SearchResult]:
        """
        Ищет релевантные чанки (гибридно: векторы + BM25, объединение через RRF).

        Args:
            query: Поисковый запрос
            n_results: Количество результатов
            file_filter: Фильтр по файлам (опционально)

        Returns:
            list[SearchResult]: Список результатов, отсортированный по RRF-оценке.

        Note:
            Если эмбеддинги посчитать не удалось, векторная часть молча деградирует
            до пустой, а поиск продолжает работать на одном BM25. Так RAG не падает
            при недоступном эмбеддере; причина пишется в лог.
        """
        candidate_n = max(n_results, n_results * 3)
        vector_results = await self._vector_search(query, candidate_n, file_filter)
        bm25_results = self._bm25_search(query, candidate_n, file_filter)

        fused = _reciprocal_rank_fusion(vector_results, bm25_results)
        results = fused[:n_results]
        logger.debug(
            f"Найдено результатов: {len(results)} (векторы {len(vector_results)}, BM25 {len(bm25_results)})"
        )
        return results

    async def _vector_search(
        self,
        query: str,
        n_results: int,
        file_filter: list[str] | None,
    ) -> list[SearchResult]:
        """Векторный поиск; при недоступных эмбеддингах возвращает пустой список."""
        try:
            query_embedding = await self._embed([query])
        except EmbedderError as e:
            logger.warning(f"Векторный RAG-поиск недоступен: {e}")
            return []

        where = where_file_paths(file_filter) if file_filter else None
        page = self.collection.query(
            embedding=query_embedding[0],
            n_results=n_results,
            where=where,
        )

        ids = page["ids"]
        documents = page["documents"]
        metadatas = page["metadatas"]
        distances = page["distances"]

        results: list[SearchResult] = []
        if ids and documents and metadatas and distances:
            for i, doc in enumerate(documents):
                score = max(0.0, min(1.0, 1 - distances[i]))  # Cosine distance -> similarity
                results.append(self._make_result(ids[i], doc, metadatas[i], score))
        return results

    def _bm25_search(
        self,
        query: str,
        n_results: int,
        file_filter: list[str] | None,
    ) -> list[SearchResult]:
        """Лексический поиск BM25; учитывает ``file_filter``."""
        self._ensure_bm25()
        if self._bm25 is None:
            return []

        allowed = set(file_filter) if file_filter else None
        ranked = self._bm25.search(query, top_k=len(self._bm25_docs))

        results: list[SearchResult] = []
        for index, score in ranked:
            metadata = self._bm25_metas[index]
            if allowed is not None and metadata["file_path"] not in allowed:
                continue
            results.append(self._make_result(self._bm25_ids[index], self._bm25_docs[index], metadata, score))
            if len(results) >= n_results:
                break
        return results

    def _ensure_bm25(self) -> None:
        """Строит BM25-индекс по всем чанкам коллекции, если он ещё не построен."""
        if self._bm25 is not None:
            return
        ids, documents, metadatas = self.collection.get_all()
        index = BM25Index()
        index.build(documents)
        self._bm25 = index
        self._bm25_ids = ids
        self._bm25_docs = documents
        self._bm25_metas = metadatas
        logger.debug(f"BM25-индекс построен по {len(documents)} чанкам")

    def _invalidate_bm25(self) -> None:
        """Сбрасывает кэш BM25 (вызывается после изменения коллекции)."""
        self._bm25 = None
        self._bm25_ids = []
        self._bm25_docs = []
        self._bm25_metas = []

    @staticmethod
    def _make_result(
        chunk_id: str,
        document: str,
        metadata: ChunkMetadata,
        score: float,
    ) -> SearchResult:
        """Собирает `SearchResult` из сырых полей чанка."""
        chunk = TextChunk(
            id=chunk_id,
            file_path=metadata["file_path"],
            content=document,
            start_line=metadata["start_line"],
            end_line=metadata["end_line"],
            metadata={"language": metadata["language"]},
        )
        return SearchResult(chunk=chunk, score=score, file_path=metadata["file_path"])

    async def delete_by_file(self, file_paths: list[str]) -> None:
        """
        Удаляет чанки по путям файлов.

        Args:
            file_paths: Список путей к файлам
        """
        if not file_paths:
            return

        ids = self.collection.get_ids(where_file_paths(file_paths))

        if ids:
            self.collection.delete(ids)
            self._invalidate_bm25()
            logger.info(f"Удалено чанков: {len(ids)}")

    async def clear(self) -> None:
        """Очищает хранилище."""
        self.client.delete_collection("codebase")
        self.collection = ChromaCollection(
            self.client.get_or_create_collection(
                name="codebase",
                metadata={"hnsw:space": "cosine"},
            )
        )
        self._invalidate_bm25()
        logger.info("VectorStore очищен")
