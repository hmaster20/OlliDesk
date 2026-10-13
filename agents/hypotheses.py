"""Хранилище гипотез агента для проверяемого исследования проекта.

Замысел — заставить модель работать не как «читатель файлов», а как
исследователь: формулировать гипотезу об архитектуре, затем проверять её
чтением кода, отмечать подтверждённые/опровергнутые и строить на них
итоговый ответ.

Хранилище живёт на уровне сессии чата (`ChatPanel._hypotheses`), как и
`AttachedFileStore`. Каждая гипотеза имеет id, формулировку, метод
проверки и статус (open/verified/refuted/partial/inconclusive). Пока
в хранилище есть гипотезы в статусе `open`, фаза исследования не
продвигается к синтезу — модель обязана их закрыть (проверить) или
пометить inconclusive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


HypothesisStatus = Literal["open", "verified", "refuted", "partial", "inconclusive"]

# Порядок сортировки для промпта: сначала открытые (нужно проверить),
# затем сомнительные, затем закрытые.
_STATUS_PRIORITY: dict[str, int] = {
    "open": 0,
    "partial": 1,
    "inconclusive": 2,
    "refuted": 3,
    "verified": 4,
}

_STATUS_LABELS: dict[str, str] = {
    "open": "🟦 открыта",
    "verified": "✅ подтверждена",
    "refuted": "❌ опровергнута",
    "partial": "🟨 частично",
    "inconclusive": "⚪ не проверена",
}


@dataclass
class Hypothesis:
    """Одна гипотеза о проекте."""

    id: str
    statement: str
    verification_method: str
    status: HypothesisStatus = "open"
    evidence: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )


@dataclass
class HypothesisStore:
    """Набор гипотез текущей сессии чата."""

    _items: list[Hypothesis] = field(default_factory=list)
    _counter: int = 0

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)

    @property
    def items(self) -> list[Hypothesis]:
        """Копия списка гипотез (в порядке добавления)."""
        return list(self._items)

    def clear(self) -> None:
        """Очищает хранилище (при очистке чата)."""
        self._items.clear()
        self._counter = 0

    def next_id(self) -> str:
        """Возвращает следующий свободный id (h1, h2, …)."""
        self._counter += 1
        return f"h{self._counter}"

    def add(self, statement: str, verification_method: str) -> Hypothesis:
        """Добавляет гипотезу, возвращает её."""
        h = Hypothesis(
            id=self.next_id(),
            statement=statement.strip(),
            verification_method=verification_method.strip(),
        )
        self._items.append(h)
        return h

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        """Ищет гипотезу по id."""
        for h in self._items:
            if h.id == hypothesis_id:
                return h
        return None

    def update(
        self,
        hypothesis_id: str,
        status: HypothesisStatus,
        evidence: str = "",
    ) -> Hypothesis | None:
        """Обновляет статус гипотезы; ``None``, если id не найден."""
        h = self.get(hypothesis_id)
        if h is None:
            return None
        h.status = status
        if evidence:
            h.evidence = evidence.strip()
        h.updated_at = datetime.now().isoformat(timespec="seconds")
        return h

    def count_open(self) -> int:
        """Сколько гипотез со статусом ``open`` (не проверены)."""
        return sum(1 for h in self._items if h.status == "open")

    def sorted_items(self) -> list[Hypothesis]:
        """Открытые — первыми, чтобы модель видела, что осталось проверить."""
        return sorted(self._items, key=lambda h: _STATUS_PRIORITY.get(h.status, 99))

    def render_for_prompt(self, max_items: int = 15) -> str:
        """Форматирует гипотезы для системного промпта.

        Открытые показываются с методом проверки (что нужно прочитать),
        закрытые — с доказательством. Порядок — по приоритету статуса.
        """
        if not self._items:
            return ""
        lines: list[str] = []
        items = self.sorted_items()[:max_items]
        for h in items:
            label = _STATUS_LABELS.get(h.status, h.status)
            line = f"• [{h.id}] {label}: {h.statement}"
            if h.status == "open":
                line += f" → проверить: {h.verification_method}"
            elif h.evidence:
                evidence = h.evidence[:160].replace("\n", " ")
                line += f" — доказательство: {evidence}"
            lines.append(line)
        if len(self._items) > max_items:
            lines.append(f"… и ещё {len(self._items) - max_items} гипотез(ы)")
        return "\n".join(lines)

    def summary(self) -> str:
        """Короткая сводка для промпта и статус-строки."""
        if not self._items:
            return "0 гипотез"
        open_n = self.count_open()
        verified = sum(1 for h in self._items if h.status == "verified")
        return f"гипотез: {len(self._items)}, открыто: {open_n}, подтверждено: {verified}"