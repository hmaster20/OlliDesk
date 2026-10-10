"""BM25 (Okapi) для гибридного поиска по кодовой базе.

Векторный поиск хорошо ловит смысл, но промахивается по точным именам,
зарезервированным словам и путям. BM25 наоборот: находит по совпадению токенов.
Обе выдачи объединяются в `VectorStore.search` через reciprocal rank fusion.

Зависимостей нет — реализация занимает несколько десятков строк, а `rank_bm25`
в проект не тянем.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# \w с UNICODE ловит и латиницу, и кириллицу, и цифры, и `_`.
_WORD_RE = re.compile(r"\w+", re.UNICODE)
# Дробим идентификаторы: HTTPServer → HTTP + Server, userID → user + ID.
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Разбивает текст на нормализованные токены для BM25.

    Дополнительно дробит snake_case и camelCase, чтобы «get_user_id»
    находился по «user», а «HTTPServer» — по «server».

    Args:
        text: Исходный текст (код, комментарий, документация).

    Returns:
        Список токенов в нижнем регистре.
    """
    tokens: list[str] = []
    for word in _WORD_RE.findall(text):
        for chunk in word.split("_"):
            if not chunk:
                continue
            parts = _CAMEL_RE.findall(chunk) or [chunk]
            tokens.extend(part.lower() for part in parts)
    return tokens


class BM25Index:
    """Индекс Okapi BM25 по набору текстовых документов.

    Индекс строится по всему корпусу чанков и хранится в `VectorStore`
    до изменения коллекции.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        """
        Args:
            k1: Насыщение частоты терма.
            b: Нормализация по длине документа (0 — выкл., 1 — полная).
        """
        self.k1 = k1
        self.b = b
        self._doc_count = 0
        self._avgdl = 0.0
        self._doc_lengths: list[int] = []
        self._term_freqs: list[Counter[str]] = []
        self._idf: dict[str, float] = {}

    def build(self, documents: list[str]) -> None:
        """Строит индекс по документам.

        Args:
            documents: Тексты чанков в порядке следования.
        """
        self._doc_count = len(documents)
        self._term_freqs = [Counter(tokenize(doc)) for doc in documents]
        self._doc_lengths = [sum(freqs.values()) for freqs in self._term_freqs]
        total = sum(self._doc_lengths)
        self._avgdl = total / self._doc_count if self._doc_count else 0.0

        doc_freqs: Counter[str] = Counter()
        for freqs in self._term_freqs:
            doc_freqs.update(freqs.keys())
        n = self._doc_count
        self._idf = {term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in doc_freqs.items()}

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Возвращает ``top_k`` документов, лучших по BM25.

        Args:
            query: Поисковый запрос.
            top_k: Сколько результатов вернуть.

        Returns:
            Список пар ``(индекс документа, оценка)`` по убыванию оценки.
            Индексы указывают на порядок, в котором документы пришли в ``build``.
        """
        if not self._doc_count or not self._avgdl:
            return []

        scores = [0.0] * self._doc_count
        for term in tokenize(query):
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i, freqs in enumerate(self._term_freqs):
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                dl = self._doc_lengths[i]
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
                scores[i] += idf * (tf * (self.k1 + 1)) / denom

        ranked = [(i, score) for i, score in enumerate(scores) if score > 0]
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked[:top_k]
