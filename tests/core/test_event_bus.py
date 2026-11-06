"""Тесты `core.event_bus` и loguru-sink (2.5)."""

from __future__ import annotations

import pytest

from core.event_bus import EventBus, emit_event, subscribe


@pytest.fixture(autouse=True)
def _clean_bus():
    """Изолирует тесты: снимает подписчиков до и после каждого теста."""
    bus = EventBus.instance()
    bus.clear()
    yield
    bus.clear()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_instance_is_singleton() -> None:
    assert EventBus.instance() is EventBus.instance()


# ---------------------------------------------------------------------------
# subscribe / emit
# ---------------------------------------------------------------------------


def test_emit_calls_subscriber() -> None:
    received: list[tuple[str, dict]] = []
    unsub = subscribe("tool.started", lambda k, p: received.append((k, p)))
    emit_event("tool.started", {"name": "read_file", "args_hash": "abc"})
    assert received == [("tool.started", {"name": "read_file", "args_hash": "abc"})]
    unsub()


def test_emit_wildcard_receives_all() -> None:
    received: list[str] = []
    unsub = subscribe(None, lambda k, _p: received.append(k))
    emit_event("tool.started", {})
    emit_event("tool.finished", {})
    emit_event("llm.done", {})
    assert received == ["tool.started", "tool.finished", "llm.done"]
    unsub()


def test_emit_specific_only_matches() -> None:
    received: list[str] = []
    unsub = subscribe("tool.started", lambda k, _p: received.append(k))
    emit_event("tool.started", {})
    emit_event("tool.finished", {})  # не должен прийти
    emit_event("llm.done", {})  # не должен прийти
    assert received == ["tool.started"]
    unsub()


def test_unsubscribe_stops_delivery() -> None:
    received: list[str] = []
    unsub = subscribe("tool.started", lambda k, _p: received.append(k))
    emit_event("tool.started", {})
    unsub()
    emit_event("tool.started", {})
    assert len(received) == 1


def test_unsubscribe_is_idempotent() -> None:
    calls: list[str] = []
    unsub = subscribe("x.y", lambda k, _p: calls.append(k))
    unsub()
    unsub()  # повторный вызов не должен падать
    emit_event("x.y", {})
    assert calls == []


# ---------------------------------------------------------------------------
# Устойчивость к ошибкам подписчиков
# ---------------------------------------------------------------------------


def test_broken_subscriber_does_not_stop_others() -> None:
    received: list[str] = []

    def broken(_k: str, _p: dict) -> None:
        raise RuntimeError("boom")

    unsub1 = subscribe("x.y", broken)
    unsub2 = subscribe("x.y", lambda k, _p: received.append(k))
    emit_event("x.y", {})  # не должно бросить наружу
    assert received == ["x.y"]
    unsub1()
    unsub2()


# ---------------------------------------------------------------------------
# Порядок подписчиков: wildcard → specific
# ---------------------------------------------------------------------------


def test_wildcard_first_then_specific() -> None:
    order: list[str] = []
    unsub_a = subscribe(None, lambda _k, _p: order.append("wildcard"))
    unsub_b = subscribe("x.y", lambda _k, _p: order.append("specific"))
    emit_event("x.y", {})
    assert order == ["wildcard", "specific"]
    unsub_a()
    unsub_b()


# ---------------------------------------------------------------------------
# Без дедлока: подписчик, эмитящий ещё одно событие
# ---------------------------------------------------------------------------


def test_reentrant_emit_no_deadlock() -> None:
    """Подписчик на X эмитит Y — не должно давать дедлок на RLock."""
    received: list[str] = []

    def handler_x(kind: str, _p: dict) -> None:
        received.append("x")
        emit_event("y.done", {})

    unsub_x = subscribe("x.done", handler_x)
    unsub_y = subscribe("y.done", lambda k, _p: received.append("y"))
    emit_event("x.done", {})
    assert received == ["x", "y"]
    unsub_x()
    unsub_y()


# ---------------------------------------------------------------------------
# subscriber_count
# ---------------------------------------------------------------------------


def test_subscriber_count() -> None:
    bus = EventBus.instance()
    assert bus.subscriber_count() == 0
    unsub1 = subscribe("a.b", lambda *_: None)
    unsub2 = subscribe(None, lambda *_: None)
    assert bus.subscriber_count("a.b") == 1
    assert bus.subscriber_count() == 2
    unsub1()
    unsub2()
    assert bus.subscriber_count() == 0


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_removes_all() -> None:
    bus = EventBus.instance()
    subscribe("a.b", lambda *_: None)
    subscribe(None, lambda *_: None)
    bus.clear()
    assert bus.subscriber_count() == 0


# ---------------------------------------------------------------------------
# Loguru-sink (интеграция)
# ---------------------------------------------------------------------------


def test_loguru_sink_emits_info() -> None:
    """logger.info(...) попадает в EventBus как kind='log'."""
    from loguru import logger as _logger

    from core.logger import _event_bus_sink

    events: list[tuple[str, dict]] = []
    unsub = subscribe(None, lambda k, p: events.append((k, p)))

    sink_id = _logger.add(_event_bus_sink, level="INFO", format="{message}")
    try:
        _logger.info("eventbus-test-marker-1")
        _logger.debug("eventbus-test-marker-2")  # ниже порога — не должно попасть
    finally:
        _logger.remove(sink_id)
        unsub()

    log_events = [(k, p) for k, p in events if k == "log"]
    messages = [p["message"] for _k, p in log_events]
    assert "eventbus-test-marker-1" in messages
    assert "eventbus-test-marker-2" not in messages


def test_loguru_sink_redacts_secret() -> None:
    """Секреты не утекают в EventBus (0.1.2 + 2.5)."""
    from loguru import logger as _logger

    from core.logger import _event_bus_sink, _redact_filter

    events: list[tuple[str, dict]] = []
    unsub = subscribe(None, lambda k, p: events.append((k, p)))

    sink_id = _logger.add(
        _event_bus_sink,
        level="INFO",
        format="{message}",
        filter=_redact_filter,
    )
    try:
        _logger.info("token is TOKEN=abcdef123456")
    finally:
        _logger.remove(sink_id)
        unsub()

    log_msgs = [p["message"] for k, p in events if k == "log"]
    assert not any("abcdef123456" in m for m in log_msgs)
    assert any("[REDACTED:env_secret]" in m for m in log_msgs)

    # ---------------------------------------------------------------------------


# 2.5: монотонный _seq
# ---------------------------------------------------------------------------


def test_seq_monotonic() -> None:
    """Каждый emit получает _seq, счётчик монотонно растёт."""
    received: list[int] = []
    unsub = subscribe(None, lambda _k, p: received.append(p["_seq"]))
    try:
        emit_event("x.a", {})
        emit_event("x.b", {})
        emit_event("x.c", {})
    finally:
        unsub()
    assert received == sorted(received)
    assert len(set(received)) == 3


def test_seq_in_copy_not_original() -> None:
    """_seq добавляется в копию — оригинальный payload не мутируется."""
    original = {"name": "read_file"}
    unsub = subscribe("tool.started", lambda _k, p: None)
    try:
        emit_event("tool.started", original)
    finally:
        unsub()
    assert "_seq" not in original
    assert original == {"name": "read_file"}


def test_seq_respected_if_present() -> None:
    """Если эмиттер положил _seq сам — уважаем (не перезаписываем)."""
    received: list[int] = []
    unsub = subscribe("x.y", lambda _k, p: received.append(p["_seq"]))
    try:
        emit_event("x.y", {"_seq": 999})
    finally:
        unsub()
    assert received == [999]


def test_seq_survives_clear() -> None:
    """clear() не сбрасывает счётчик — seq монотонен «за процесс»."""
    bus = EventBus.instance()
    received: list[int] = []
    bus.clear()
    unsub = bus.subscribe(None, lambda _k, p: received.append(p["_seq"]))
    try:
        bus.emit("a.b", {})
        bus.clear()
        unsub2 = bus.subscribe(None, lambda _k, p: received.append(p["_seq"]))
        try:
            bus.emit("a.c", {})
        finally:
            unsub2()
    finally:
        unsub()
    assert received[0] < received[1]
