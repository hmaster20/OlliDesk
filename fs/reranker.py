"""Реранкинг RAG-выдачи кросс-энкодером (sentence-transformers).

Кросс-энкодер оценивает пару (запрос, чанк) совместно и точнее би-энкодера
векторного поиска, но дороже: модель считается на CPU. Поэтому реранк
применяется только к верхушке кандидатов после RRF (см. `VectorStore`) и
включается явно (`RerankSettings.enabled`).

Тяжёлая зависимость (torch через sentence-transformers) импортируется лениво
внутри `_load`, а сама модель скачивается при первом запросе — так импорт
`fs.reranker` и старт приложения не тянут torch.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from fs.vector_store import SearchResult

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


class CrossEncoderReranker:
    """Обёртка над `sentence_transformers.CrossEncoder` для реранкинга RAG.

    Реализует протокол `fs.vector_store.Reranker`. Модель загружается лениво
    при первом `rerank()`.
    """

    def __init__(self, model_name: str, max_length: int = 512) -> None:
        """
        Args:
            model_name: HF-id модели-кросс-энкодера.
            max_length: Максимальная длина пары (запрос + чанк) в токенах.
        """
        self.model_name = model_name
        self.max_length = max_length
        self._model: CrossEncoder | None = None

    def _load(self) -> CrossEncoder:
        """Лениво загружает (и при первом вызове скачивает) модель."""
        if self._model is None:
            from sentence_transformers import CrossEncoder

            logger.info(f"Загрузка реранкера: {self.model_name}")
            self._model = CrossEncoder(self.model_name, max_length=self.max_length)
        return self._model

    async def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        """Переупорядочивает кандидатов по релевантности пары (запрос, чанк).

        Args:
            query: Поисковый запрос.
            results: Кандидаты после RRF.

        Returns:
            Тот же набор результатов, отсортированный по убыванию оценки
            кросс-энкодера.
        """
        if len(results) <= 1:
            return results
        return await asyncio.to_thread(self._rerank_sync, query, results)

    def _rerank_sync(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        """Синхронный реранк (выполняется в отдельном потоке)."""
        model = self._load()
        pairs = [[query, r.chunk.content] for r in results]
        scores = model.predict(pairs)
        order = sorted(range(len(results)), key=lambda i: float(scores[i]), reverse=True)
        return [results[i] for i in order]
