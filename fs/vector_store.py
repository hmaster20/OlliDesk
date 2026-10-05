"""Векторное хранилище на основе ChromaDB."""

from pathlib import Path
from typing import Protocol, runtime_checkable

import chromadb
from chromadb.config import Settings
from loguru import logger
from pydantic import BaseModel

from core.exceptions import EmbedderError
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

        logger.info(f"Всего добавлено чанков: {len(chunks)}")

    async def search(
        self,
        query: str,
        n_results: int = 10,
        file_filter: list[str] | None = None,
    ) -> list[SearchResult]:
        """
        Ищет релевантные чанки.

        Args:
            query: Поисковый запрос
            n_results: Количество результатов
            file_filter: Фильтр по файлам (опционально)

        Returns:
            list[SearchResult]: Список результатов

        Note:
            Если эмбеддинги посчитать не удалось, возвращается пустой список:
                RAG деградирует до «поиск по контексту не работает», но чат
                и индексация не падают. Причина пишется в лог.
        """
        try:
            query_embedding = await self._embed([query])
        except EmbedderError as e:
            logger.warning(f"RAG-поиск недоступен: {e}")
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

        search_results: list[SearchResult] = []

        if ids and documents and metadatas and distances:
            for i, doc in enumerate(documents):
                metadata = metadatas[i]
                score = max(0.0, min(1.0, 1 - distances[i]))  # Cosine distance -> similarity

                chunk = TextChunk(
                    id=ids[i],
                    file_path=metadata["file_path"],
                    content=doc,
                    start_line=metadata["start_line"],
                    end_line=metadata["end_line"],
                    metadata={"language": metadata["language"]},
                )

                search_results.append(
                    SearchResult(
                        chunk=chunk,
                        score=score,
                        file_path=metadata["file_path"],
                    )
                )

        logger.debug(f"Найдено результатов: {len(search_results)}")
        return search_results

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
        logger.info("VectorStore очищен")
