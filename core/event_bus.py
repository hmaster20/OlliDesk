"""Потокобезопасный EventBus OlliDesk (2.5).

Зачем: логи информативнее UI — пользователь не понимает, почему
приложение «зависло», когда идёт RAG-поиск / tool-call / синтез 7B на
CPU. `EventBus` — единая точка, куда модули (`fs`, `agents`, `ui`)
эмитят структурированные события, а UI подписывается и показывает
«живую» ленту.

Принципы:
  * события эмитятся из ЛЮБОГО потока (worker, GUI, asyncio);
  * это **plain-Python** синглтон, не `QObject`: `fs/` и `agents/` не
    должны тянуть PySide6, тесты работают без `QApplication`;
  * UI-подписка — через `ui.widgets.live_events.QtEventBus` (QObject-мост:
    переиздаёт события как Qt-сигнал, Qt сам выберет QueuedConnection
    для GUI-потока);
  * `emit()` НЕ держит `lock` во время вызова подписчиков — иначе
    подписчик, вызывающий `emit()` рекурсивно, получит дедлок;
  * ошибка подписчика логируется и проглатывается — одно упавшее
    UI-событие не должно валить worker-поток.

Схема событий — `core/events.py` (`EventKind` + TypedDict-контракты).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from loguru import logger

# Подписчик: (kind, payload) -> None. Payload — dict (см. core.events).
EventCallback = Callable[[str, dict], None]


class EventBus:
    """Потокобезопасный синглтон с подпиской по виду события."""

    _instance: EventBus | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        # dict[str | None, list[EventCallback]]: None — подписка на все.
        self._subscribers: dict[str | None, list[EventCallback]] = {}
        # RLock, а не Lock: подписчик может рекурсивно вызвать subscribe
        # (например, при первом событии зарегистрировать обработчик фазы).
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> EventBus:
        """Возвращает глобальный экземпляр (создаёт при первом вызове)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def subscribe(
        self,
        kind: str | None,
        callback: EventCallback,
    ) -> Callable[[], None]:
        """Подписывает `callback` на события.

        Args:
            kind: Вид события (см. ``core.events.EventKind``) или ``None``
                для подписки на ВСЕ события.
            callback: ``(kind: str, payload: dict) -> None``. Вызывается
                синхронно из потока-эмиттера. Для UI используйте
                ``QtEventBus`` — он сам завернёт в Qt-сигнал с queued
                connection на GUI-поток.

        Returns:
            Функция отписки. Идемпотентна: повторный вызов — no-op.
        """
        with self._lock:
            self._subscribers.setdefault(kind, []).append(callback)

        def unsubscribe() -> None:
            with self._lock:
                lst = self._subscribers.get(kind)
                if lst and callback in lst:
                    lst.remove(callback)

        return unsubscribe

    def emit(self, kind: str, payload: dict) -> None:
        """Эмитит событие синхронно из текущего потока.

        Snapshot подписчиков берётся ПОД lock, сами callback'и вызываются
        ВНЕ lock — иначе рекурсивный `emit()` из подписчика дал бы дедлок
        на `_lock`.

        Args:
            kind: Вид события (``core.events.EventKind``).
            payload: Тело события (dict, контракт — см. `core.events`).
        """
        with self._lock:
            all_subs = list(self._subscribers.get(None, []))
            specific_subs = list(self._subscribers.get(kind, []))
        for cb in all_subs + specific_subs:
            try:
                cb(kind, payload)
            except Exception:
                logger.exception(f"EventBus: подписчик упал на {kind!r}")

    def clear(self) -> None:
        """Снимает всех подписчиков (для тестов)."""
        with self._lock:
            self._subscribers.clear()

    def subscriber_count(self, kind: str | None = None) -> int:
        """Сколько подписчиков у события (для диагностики/тестов).

        Args:
            kind: Конкретный вид события. ``None`` (по умолчанию) — сумма
                по всем ключам, включая подписчиков на все события.
        """
        with self._lock:
            if kind is None:
                return sum(len(v) for v in self._subscribers.values())
            return len(self._subscribers.get(kind, []))


# ---------------------------------------------------------------------------
# Module-level convenience — избавляет от .instance() в горячих путях.
# ---------------------------------------------------------------------------


def emit_event(kind: str, payload: dict) -> None:
    """Удобная обёртка: ``EventBus.instance().emit(kind, payload)``."""
    EventBus.instance().emit(kind, payload)


def subscribe(kind: str | None, callback: EventCallback) -> Callable[[], None]:
    """Удобная обёртка: ``EventBus.instance().subscribe(kind, callback)``."""
    return EventBus.instance().subscribe(kind, callback)


__all__ = [
    "EventBus",
    "EventCallback",
    "emit_event",
    "subscribe",
]