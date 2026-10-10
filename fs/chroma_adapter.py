"""Тонкая типизированная обёртка над коллекцией ChromaDB.

Зачем отдельный модуль: chromadb формально поставляет аннотации (`py.typed` есть),
но в сигнатурах `Collection.add` / `query` использует инвариантные `List[...]`
внутри `Union`, из-за чего корректные с точки зрения Python значения
(`list[list[float]]`, `list[dict[str, str | int]]`) mypy отвергает. Размазывать
по коду `cast(Any, ...)` и `# type: ignore` не хочется — вся небезопасная граница
с библиотекой собрана здесь, в одном месте и с пояснениями.

Наружу адаптер отдаёт собственные типы (`QueryPage`, `ChunkMetadata`), поэтому
`fs/vector_store.py` полностью типизирован и не знает про внутренности chromadb.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast

from chromadb.api.models.Collection import Collection
from chromadb.api.types import IncludeEnum, Where


class ChunkMetadata(TypedDict):
    """Метаданные чанка в том виде, в каком их кладёт `VectorStore`."""

    file_path: str
    start_line: int
    end_line: int
    language: str


class QueryPage(TypedDict):
    """Развёрнутые результаты `query()` для одного вектора запроса.

    chromadb отдаёт внешний список «по одному на запрос»; адаптер снимает
    этот уровень, поэтому наружу идут плоские списки.
    """

    ids: list[str]
    documents: list[str]
    metadatas: list[ChunkMetadata]
    distances: list[float]


def where_file_paths(paths: list[str]) -> Where:
    """Собирает `where`-фильтр «файл_path входит в список».

    Args:
        paths: Список путей файлов.

    Returns:
        Фильтр в формате chromadb.
    """
    # Ключ `$in` внутри — литерал, который mypy не может вывести из обычного словаря,
    # поэтому приводим к Where явно (это ровно то, что проверяет сам chromadb в рантайме).
    return cast(Where, {"file_path": {"$in": paths}})


class ChromaCollection:
    """Обёртка над `Collection` с типами, удобными для вызывающего кода."""

    def __init__(self, collection: Collection) -> None:
        """
        Args:
            collection: Коллекция chromadb.
        """
        self._collection = collection

    @property
    def name(self) -> str:
        """Имя коллекции."""
        return self._collection.name

    def count(self) -> int:
        """Сколько чанков сейчас в индексе."""
        return self._collection.count()

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[ChunkMetadata],
    ) -> None:
        """Добавляет чанки в коллекцию.

        Args:
            ids: Идентификаторы чанков.
            embeddings: По одному вектору на чанк.
            documents: Тексты чанков.
            metadatas: Метаданные чанков.
        """
        self._collection.add(
            ids=ids,
            # Дефект аннотаций chromadb: `List[Union[Sequence[float], Sequence[int]]]`
            # инвариантен, поэтому `list[list[float]]` не проходит проверку.
            embeddings=embeddings,  # type: ignore[arg-type]
            documents=documents,
            # Тот же дефект: `List[Mapping[str, ...]]` вместо `Sequence[Mapping[str, ...]]`.
            metadatas=metadatas,  # type: ignore[arg-type]
        )

    def query(
        self,
        embedding: list[float],
        n_results: int,
        where: Where | None,
    ) -> QueryPage:
        """Ищет ближайшие чанки по одному вектору.

        Args:
            embedding: Вектор запроса.
            n_results: Сколько результатов вернуть.
            where: `where`-фильтр или `None`.

        Returns:
            Развёрнутые подряд идущие результаты (в `fs` идёт ровно один запрос).
        """
        raw = self._collection.query(
            # Тот же инвариантный List, что и в add().
            query_embeddings=[embedding],  # type: ignore[arg-type]
            n_results=n_results,
            where=where,
            include=[IncludeEnum.documents, IncludeEnum.metadatas, IncludeEnum.distances],
        )

        return QueryPage(
            ids=_first_or_empty(raw.get("ids")),
            documents=_first_or_empty(raw.get("documents")),
            metadatas=_first_or_empty(raw.get("metadatas")),
            distances=_first_or_empty(raw.get("distances")),
        )

    def get_all(self) -> tuple[list[str], list[str], list[ChunkMetadata]]:
        """Возвращает все чанки коллекции (для построения BM25-индекса).

        Returns:
            Кортеж ``(ids, documents, metadatas)`` одинаковой длины.
        """
        raw = self._collection.get(
            include=[IncludeEnum.documents, IncludeEnum.metadatas],
        )
        ids = cast(list[str], raw.get("ids") or [])
        documents = cast(list[str], raw.get("documents") or [])
        metadatas = cast(list[ChunkMetadata], raw.get("metadatas") or [])
        return ids, documents, metadatas

    def get_ids(self, where: Where) -> list[str]:
        """Возвращает идентификаторы чанков, попадающих под фильтр.

        Args:
            where: `where`-фильтр.

        Returns:
            Список идентификаторов (может быть пустым).
        """
        raw = self._collection.get(where=where, include=[])
        return cast(list[str], raw.get("ids") or [])

    def delete(self, ids: list[str]) -> None:
        """Удаляет чанки по идентификаторам.

        Args:
            ids: Идентификаторы чанков.
        """
        self._collection.delete(ids=ids)


def _first_or_empty(value: object) -> Any:
    """Достаёт первый вложенный список из ответа chromadb.

    chromadb возвращает результат по запросу во внешнем списке: один запрос —
    один список значений. Здесь берём этот список, а если данных нет, отдаём
    пустой, чтобы вызывающий код не проверял `None` на каждом поле.

    Args:
        value: Значение из ответа ``query()``.

    Returns:
        Список значений первого запроса либо пустой список.
    """
    if isinstance(value, list) and value and isinstance(value[0], list):
        return value[0]
    return []
