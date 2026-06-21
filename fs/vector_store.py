"""Векторное хранилище на основе ChromaDB."""

from pathlib import Path

import chromadb
from chromadb.config import Settings
from loguru import logger
from pydantic import BaseModel

from agents.ollama_client import OllamaClient
from fs.indexer import TextChunk


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
        embed_client: OllamaClient,
        embed_model: str,
    ):
        """
        Инициализация хранилища.

        Args:
            persist_dir: Папка для хранения ChromaDB
            embed_client: Клиент Ollama для эмбеддингов
            embed_model: Имя модели для эмбеддингов
        """
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
        self.collection = self.client.get_or_create_collection(
            name="codebase",
            metadata={"hnsw:space": "cosine"},
        )

        logger.info(f"VectorStore инициализирован: {self.persist_dir}")

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
            batch = chunks[i:i + batch_size]

            # Векторизация
            texts = [chunk.content for chunk in batch]
            embeddings = await self.embed_client.embed(self.embed_model, texts)

            # Подготовка данных для ChromaDB
            ids = [chunk.id for chunk in batch]
            documents = [chunk.content for chunk in batch]
            metadatas = [
                {
                    "file_path": chunk.file_path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "language": chunk.metadata.get("language", "unknown"),
                }
                for chunk in batch
            ]

            # Добавление в коллекцию
            self.collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            ) # type: ignore

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
        """
        # Векторизация запроса
        query_embedding = await self.embed_client.embed(self.embed_model, [query])

        # Подготовка фильтра
        where_filter = None
        if file_filter:
            where_filter = {"file_path": {"$in": file_filter}}

        # Поиск
        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=n_results,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )  # type: ignore

        # Преобразование результатов
        search_results = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                metadata = results["metadatas"][0][i]
                distance = results["distances"][0][i]
                score = max(0.0, min(1.0, 1 - distance))  # Cosine distance -> similarity

                chunk = TextChunk(
                    id=results["ids"][0][i],
                    file_path=metadata["file_path"],
                    content=doc,
                    start_line=metadata["start_line"],
                    end_line=metadata["end_line"],
                    metadata={"language": metadata.get("language", "unknown")},
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

        # Получение ID чанков для удаления
        results = self.collection.get(
            where={"file_path": {"$in": file_paths}},
            include=[],
        )

        if results["ids"]:
            self.collection.delete(ids=results["ids"])
            logger.info(f"Удалено чанков: {len(results['ids'])}")

    async def clear(self) -> None:
        """Очищает хранилище."""
        self.client.delete_collection("codebase")
        self.collection = self.client.get_or_create_collection(
            name="codebase",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("VectorStore очищен")
