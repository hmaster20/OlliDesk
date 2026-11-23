"""Векторное хранилище на основе ChromaDB."""

import hashlib
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import chromadb
from chromadb.config import Settings
from loguru import logger
from pydantic import BaseModel

from core.event_bus import emit_event
from core.events import EventKind
from core.exceptions import EmbedderError
from fs.bm25 import BM25Index
from fs.chroma_adapter import (
    ChromaCollection,
    ChunkMetadata,
    QueryPage,
    build_where,
    where_file_paths,
)
from fs.indexer import TextChunk

_DEFAULT_COLLECTION = "codebase"

# [9.4] Категория "docs": markdown/rst/текст + документы (PDF/DOCX/…).
_DOC_LANGUAGES = {
    "markdown",
    "rst",
    "text",
    "txt",
    "csv",
    "properties",
    "pdf",
    "docx",
    "pptx",
    "xlsx",
    "rtf",
}

CATEGORIES = ("code", "docs")


def category_for_language(language: str) -> str:
    """Определяет категорию файла по языку: ``docs`` для документации, иначе ``code``.

    Args:
        language: Значение из ``language`` метаданных чанка
            (см. `fs.indexer.FileIndexer._detect_language`).

    Returns:
        ``"docs"`` для markdown/rst/text/txt, иначе ``"code"``.
    """
    return "docs" if language in _DOC_LANGUAGES else "code"


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


@runtime_checkable
class Reranker(Protocol):
    """Контракт реранкера RAG-выдачи.

    Реранкер получает кандидатов после слияния (RRF) и возвращает их в новом
    порядке — от более релевантных к менее. Подойдёт любой объект с методом
    ``async rerank(query, results)``; конкретная реализация (кросс-энкодер)
    живёт в `fs/reranker.py`.
    """

    async def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        """Переупорядочивает кандидатов по релевантности запросу."""
        ...


@runtime_checkable
class RerankCache(Protocol):
    """Кэш порядка после реранка: ключ → список id чанков.

    Ключ строится из запроса и содержимого кандидатов, поэтому попадание в кэш
    безопасно: при изменении чанка меняется и ключ. Реализация по умолчанию —
    `core.search_cache.SearchCache` (методы ``get_list``/``set_list``).
    """

    def get_list(self, key: str, max_age_hours: int = 24) -> list[str] | None:
        """Возвращает сохранённый порядок id или ``None`` при промахе."""
        ...

    def set_list(self, key: str, values: list[str]) -> None:
        """Сохраняет порядок id."""
        ...


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
        reranker: Reranker | None = None,
        rerank_pool: int = 20,
        embed_models: dict[str, str] | None = None,
        rerank_cache: RerankCache | None = None,
    ):
        """
        Инициализация хранилища.

        Args:
            persist_dir: Папка для хранения ChromaDB
            embed_client: Объект с методом ``embed(model, texts)`` —
                `OllamaClient`, `LocalLLMClient` или `ModelRouter`
            embed_model: Модель эмбеддингов по умолчанию (для всех категорий,
                где отдельная модель не задана)
            reranker: Необязательный реранкер (см. `Reranker`); при ``None``
                порядок после RRF отдаётся как есть
            rerank_pool: Сколько лучших кандидатов после RRF подавать на реранк
            embed_models: Необязательная карта ``категория -> модель`` для
                раздельных эмбеддингов (см. `category_for_language`). Каждая
                модель получает свою коллекцию (векторы разных моделей
                несравнимы), а выдачи объединяются через RRF. Пустое значение
                категории означает ``embed_model``.
            rerank_cache: Необязательный кэш порядка после реранка (см.
                `RerankCache`); при ``None`` результат реранка не кэшируется.

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
        self.reranker = reranker
        self.rerank_cache = rerank_cache
        self.rerank_pool = max(1, rerank_pool)

        override = embed_models or {}
        self.embed_models: dict[str, str] = {
            category: (override.get(category) or embed_model) for category in CATEGORIES
        }

        # Инициализация ChromaDB
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )

        # По коллекции на каждую уникальную модель (открываем заранее, чтобы поиск
        # работал и в новом процессе).
        self._collections: dict[str, ChromaCollection] = {}
        self._ensure_collections()

        # BM25-индекс строится лениво из всех чанков и сбрасывается при изменении
        # коллекции (см. `_invalidate_bm25`).
        self._bm25: BM25Index | None = None
        self._bm25_ids: list[str] = []
        self._bm25_docs: list[str] = []
        self._bm25_metas: list[ChunkMetadata] = []

        logger.info(
            f"VectorStore инициализирован: {self.persist_dir} "
            f"(модели эмбеддингов: {sorted(set(self.embed_models.values()))})"
        )

    @property
    def collection(self) -> ChromaCollection:
        """Коллекция модели эмбеддингов по умолчанию (обратная совместимость)."""
        return self._get_collection(self.embed_model)

    def _ensure_collections(self) -> None:
        """Открывает коллекции для всех моделей из ``embed_models``."""
        for model in sorted(set(self.embed_models.values())):
            self._get_collection(model)

    def _collection_name(self, model: str) -> str:
        """Имя коллекции для модели: ``codebase`` для модели по умолчанию, иначе с хэшем."""
        if model == self.embed_model:
            return _DEFAULT_COLLECTION
        digest = hashlib.sha1(model.encode("utf-8")).hexdigest()[:8]
        return f"{_DEFAULT_COLLECTION}_{digest}"

    def _get_collection(self, model: str) -> ChromaCollection:
        """Возвращает (создавая при необходимости) коллекцию для модели."""
        collection = self._collections.get(model)
        if collection is None:
            collection = ChromaCollection(
                self.client.get_or_create_collection(
                    name=self._collection_name(model),
                    metadata={"hnsw:space": "cosine"},
                )
            )
            self._collections[model] = collection
        return collection

    def _model_for_chunk(self, chunk: TextChunk) -> str:
        """Модель эмбеддингов для чанка по его языку (см. `category_for_language`)."""
        language = str(chunk.metadata.get("language", "unknown"))
        return self.embed_models[category_for_language(language)]

    async def _embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Считает эмбеддинги и проверяет, что их пришло ровно столько, сколько текстов.

        Args:
            texts: Тексты для векторизации
            model: Модель эмбеддингов; при ``None`` — ``embed_model``

        Returns:
            Список векторов той же длины, что и ``texts``

        Raises:
            EmbedderError: Бэкенд вернул пустой результат, ошибку или
                неверное число векторов (индекс молча испортился бы).
        """
        model = model or self.embed_model
        try:
            vectors = await self.embed_client.embed(model, texts)
        except Exception as e:
            raise EmbedderError(
                f"Не удалось посчитать эмбеддинги через {type(self.embed_client).__name__}: {e}"
            ) from e

        if not vectors:
            raise EmbedderError(
                f"{type(self.embed_client).__name__} вернул пустые эмбеддинги для "
                f"{len(texts)} текстов. Проверьте, что модель эмбеддингов "
                f"('{model}') загружена и доступна."
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

        # Группируем чанки по модели эмбеддингов (категории кода/документации
        # могут обслуживаться разными моделями и жить в разных коллекциях).
        groups: dict[str, list[TextChunk]] = {}
        for chunk in chunks:
            groups.setdefault(self._model_for_chunk(chunk), []).append(chunk)

        batch_size = 32
        for model, model_chunks in groups.items():
            collection = self._get_collection(model)
            for i in range(0, len(model_chunks), batch_size):
                batch = model_chunks[i : i + batch_size]

                texts = [chunk.content for chunk in batch]
                embeddings = await self._embed(texts, model)

                ids = [chunk.id for chunk in batch]
                documents = [chunk.content for chunk in batch]
                metadatas: list[ChunkMetadata] = [
                    {
                        "file_path": chunk.file_path,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "language": str(chunk.metadata.get("language", "unknown")),
                        "mtime": float(chunk.metadata.get("mtime", 0.0)),
                        # [9.4] category — для будущей where-фильтрации.
                        # Для чанков из старых индексов её нет: пост-фильтр
                        # в `search(categories=...)` вычислит её по language.
                        "category": str(
                            chunk.metadata.get(
                                "category",
                                category_for_language(str(chunk.metadata.get("language", "unknown"))),
                            )
                        ),
                    }
                    for chunk in batch
                ]

                collection.add(
                    ids=ids,
                    embeddings=embeddings,
                    documents=documents,
                    metadatas=metadatas,
                )

                logger.debug(f"Добавлено чанков: {len(batch)} (модель '{model}')")

        self._invalidate_bm25()
        logger.info(f"Всего добавлено чанков: {len(chunks)}")

    async def search(
        self,
        query: str,
        n_results: int = 10,
        file_filter: list[str] | None = None,
        language: str | None = None,
        path_prefix: str | None = None,
        modified_after: float | None = None,
        modified_before: float | None = None,
        categories: list[str] | None = None,
    ) -> list[SearchResult]:
        """
        Ищет релевантные чанки (гибридно: векторы + BM25, объединение через RRF).

        Args:
            query: Поисковый запрос
            n_results: Количество результатов
            file_filter: Фильтр по конкретным файлам (опционально)
            language: Фильтр по языку программирования, например ``python`` (опционально)
            path_prefix: Ограничить путями, начинающимися с префикса (опционально)
            modified_after: Только файлы, изменённые не раньше этого unix-времени (опционально)
            modified_before: Только файлы, изменённые не позже этого unix-времени (опционально)
            categories: [9.4] Ограничить категории чанков: ``"code"`` / ``"docs"``.
                Фильтр применяется ПОСЛЕ RRF (по языку чанка — см.
                `category_for_language`), но ДО реранкера. Если задан —
                число кандидатов увеличивается, чтобы после отсева
                хватило результатов.

        Returns:
            list[SearchResult]: Список результатов, отсортированный по RRF-оценке.

        Note:
            Если эмбеддинги посчитать не удалось, векторная часть молча деградирует
            до пустой, а поиск продолжает работать на одном BM25. Так RAG не падает
            при недоступном эмбеддере; причина пишется в лог. Фильтр по дате работает
            только для чанков, проиндексированных с ``mtime`` (нужна переиндексация).
        """
        # [9.4] Категориальный фильтр уменьшает число результатов — увеличиваем
        # число кандидатов, чтобы после отсева хватило n_results.
        multiplier = 3 if categories else 1
        candidate_n = max(n_results * multiplier, n_results * 3)

        # 2.5: событие для «живого» UI.
        started_at = time.perf_counter()
        emit_event(
            EventKind.RAG_SEARCH_STARTED.value,
            {"query": query, "n_results": n_results},
        )

        vector_lists = await self._vector_search_lists(
            query, candidate_n, file_filter, language, path_prefix, modified_after, modified_before
        )
        bm25_results = self._bm25_search(
            query, candidate_n, file_filter, language, path_prefix, modified_after, modified_before
        )

        fused = _reciprocal_rank_fusion(*vector_lists, bm25_results)

        # [9.4] Фильтр по категориям (code/docs) — по языку чанка.
        if categories:
            allowed = set(categories)
            fused = [
                r
                for r in fused
                if category_for_language(str(r.chunk.metadata.get("language", ""))) in allowed
            ]

        results = await self._maybe_rerank(query, fused, n_results)
        vector_count = sum(len(results_) for results_ in vector_lists)
        logger.debug(
            f"Найдено результатов: {len(results)} "
            f"(векторы {vector_count}, BM25 {len(bm25_results)}, "
            f"категории {categories or 'все'})"
        )

        # 2.5: результат RAG-поиска — в EventBus.
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        if results:
            emit_event(
                EventKind.RAG_SEARCH_FOUND.value,
                {
                    "query": query,
                    "n_results": len(results),
                    "duration_ms": duration_ms,
                },
            )
        else:
            emit_event(
                EventKind.RAG_SEARCH_EMPTY.value,
                {"query": query, "duration_ms": duration_ms},
            )
        return results

    async def _maybe_rerank(
        self,
        query: str,
        fused: list[SearchResult],
        n_results: int,
    ) -> list[SearchResult]:
        """Прогоняет верхушку выдачи через реранкер, если он задан.

        Реранкер считается на CPU и может упасть (нет модели, нет сети),
        поэтому ошибка не пробрасывается наружу: поиск возвращает порядок
        после RRF, а причина пишется в лог. Порядок кэшируется по ключу из
        запроса и содержимого кандидатов (см. `RerankCache`), чтобы повторные
        запросы не считали кросс-энкодер заново.
        """
        if self.reranker is None or not fused:
            return fused[:n_results]

        pool = fused[: self.rerank_pool]
        cache_key = self._rerank_key(query, pool)

        if self.rerank_cache is not None:
            cached = self.rerank_cache.get_list(cache_key)
            if cached is not None:
                ordered = self._order_by_ids(pool, cached)
                if ordered is not None:
                    logger.debug("Порядок реранка взят из кэша")
                    return ordered[:n_results]

        try:
            reranked = await self.reranker.rerank(query, pool)
        except Exception as e:
            logger.warning(f"Реранкер недоступен, использую порядок после RRF: {e}")
            reranked = pool
        else:
            if self.rerank_cache is not None:
                self.rerank_cache.set_list(cache_key, [result.chunk.id for result in reranked])
        return reranked[:n_results]

    @staticmethod
    def _rerank_key(query: str, pool: list[SearchResult]) -> str:
        """Ключ кэша реранка: запрос + id и содержимое кандидатов.

        Chunk id не зависит от содержимого (``md5(file_path:chunk_index)``),
        поэтому в ключ добавлен хэш текста — при правке файла ключ меняется и
        старый порядок не переиспользуется.
        """
        digest = hashlib.sha1(query.encode("utf-8"))
        for result in pool:
            digest.update(b"\x00")
            digest.update(result.chunk.id.encode("utf-8"))
            digest.update(b"=")
            digest.update(hashlib.sha1(result.chunk.content.encode("utf-8")).hexdigest().encode("ascii"))
        return f"rerank|{digest.hexdigest()}"

    @staticmethod
    def _order_by_ids(
        pool: list[SearchResult],
        ids: list[str],
    ) -> list[SearchResult] | None:
        """Упорядочивает кандидатов по сохранённым id; ``None`` при несовпадении набора."""
        by_id = {result.chunk.id: result for result in pool}
        if set(ids) - by_id.keys():
            return None
        ordered = [by_id[cid] for cid in ids if cid in by_id]
        ordered.extend(result for result in pool if result.chunk.id not in set(ids))
        return ordered

    async def _vector_search_lists(
        self,
        query: str,
        n_results: int,
        file_filter: list[str] | None,
        language: str | None,
        path_prefix: str | None,
        modified_after: float | None = None,
        modified_before: float | None = None,
    ) -> list[list[SearchResult]]:
        """Векторный поиск по каждой коллекции (модели).

        Возвращает по списку на каждую доступную коллекцию; коллекции, для
        которых не удалось посчитать эмбеддинги, молча пропускаются.
        """
        where = build_where(file_filter, language, modified_after, modified_before)
        search_results: list[list[SearchResult]] = []
        for model, collection in self._collections.items():
            try:
                query_embedding = await self._embed([query], model)
            except EmbedderError as e:
                logger.warning(f"Векторный RAG-поиск недоступен для модели '{model}': {e}")
                continue

            page = collection.query(
                embedding=query_embedding[0],
                n_results=n_results,
                where=where,
            )
            results = self._results_from_page(page, path_prefix)
            if results:
                search_results.append(results)
        return search_results

    @staticmethod
    def _results_from_page(page: QueryPage, path_prefix: str | None) -> list[SearchResult]:
        """Собирает `SearchResult` из страницы ChromaDB, отсекая чужой ``path_prefix``."""
        ids = page["ids"]
        documents = page["documents"]
        metadatas = page["metadatas"]
        distances = page["distances"]

        results: list[SearchResult] = []
        if ids and documents and metadatas and distances:
            for i, doc in enumerate(documents):
                metadata = metadatas[i]
                if path_prefix and not metadata["file_path"].startswith(path_prefix):
                    continue
                score = max(0.0, min(1.0, 1 - distances[i]))  # Cosine distance -> similarity
                results.append(VectorStore._make_result(ids[i], doc, metadata, score))
        return results

    def _bm25_search(
        self,
        query: str,
        n_results: int,
        file_filter: list[str] | None,
        language: str | None,
        path_prefix: str | None,
        modified_after: float | None = None,
        modified_before: float | None = None,
    ) -> list[SearchResult]:
        """Лексический поиск BM25; учитывает фильтры ``file_filter``/``language``/``path_prefix``/даты."""
        self._ensure_bm25()
        if self._bm25 is None:
            return []

        allowed = set(file_filter) if file_filter else None
        ranked = self._bm25.search(query, top_k=len(self._bm25_docs))

        results: list[SearchResult] = []
        for index, score in ranked:
            metadata = self._bm25_metas[index]
            file_path = metadata["file_path"]
            if allowed is not None and file_path not in allowed:
                continue
            if language and metadata["language"] != language:
                continue
            if path_prefix and not file_path.startswith(path_prefix):
                continue
            mtime = float(metadata.get("mtime", 0.0))
            if modified_after is not None and mtime < modified_after:
                continue
            if modified_before is not None and mtime > modified_before:
                continue
            results.append(self._make_result(self._bm25_ids[index], self._bm25_docs[index], metadata, score))
            if len(results) >= n_results:
                break
        return results

    def _ensure_bm25(self) -> None:
        """Строит BM25-индекс по чанкам всех коллекций, если он ещё не построен."""
        if self._bm25 is not None:
            return
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[ChunkMetadata] = []
        for collection in self._collections.values():
            c_ids, c_docs, c_metas = collection.get_all()
            ids.extend(c_ids)
            documents.extend(c_docs)
            metadatas.extend(c_metas)
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
        chunk_metadata: dict = {
            "language": metadata["language"],
            "mtime": float(metadata.get("mtime", 0.0)),
        }
        # [9.4] category — если сохранена в Chroma; иначе посчитаем по language
        # в пост-фильтре search(). Оставляем опциональной в metadata чанка.
        if "category" in metadata:
            chunk_metadata["category"] = metadata["category"]

        chunk = TextChunk(
            id=chunk_id,
            file_path=metadata["file_path"],
            content=document,
            start_line=metadata["start_line"],
            end_line=metadata["end_line"],
            metadata=chunk_metadata,
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

        where = where_file_paths(file_paths)
        total = 0
        for collection in self._collections.values():
            ids = collection.get_ids(where)
            if ids:
                collection.delete(ids)
                total += len(ids)

        if total:
            self._invalidate_bm25()
            logger.info(f"Удалено чанков: {total}")

    async def clear(self) -> None:
        """Очищает хранилище (все коллекции ``codebase``/``codebase_*``)."""
        existing = {collection.name for collection in self.client.list_collections()}
        for name in existing:
            if name == _DEFAULT_COLLECTION or name.startswith(f"{_DEFAULT_COLLECTION}_"):
                self.client.delete_collection(name)
        self._collections = {}
        self._ensure_collections()
        self._invalidate_bm25()
        logger.info("VectorStore очищен")
