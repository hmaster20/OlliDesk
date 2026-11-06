"""Тесты `core.events.args_hash` (2.5)."""

from __future__ import annotations

from pathlib import Path

from core.events import args_hash


def test_args_hash_deterministic() -> None:
    args = {"path": "a.py", "offset": 10, "limit": 100}
    assert args_hash(args) == args_hash(args)


def test_args_hash_stable_across_dict_order() -> None:
    a = {"path": "a.py", "offset": 10}
    b = {"offset": 10, "path": "a.py"}
    assert args_hash(a) == args_hash(b)


def test_args_hash_different_for_different_args() -> None:
    assert args_hash({"path": "a.py"}) != args_hash({"path": "b.py"})


def test_args_hash_fallback_on_unserializable() -> None:
    """Path — не сериализуется json.dumps напрямую, но default=str спасает."""
    h = args_hash({"path": Path("/tmp/x")})
    assert isinstance(h, str)
    assert len(h) == 16


def test_args_hash_length() -> None:
    assert len(args_hash({})) == 16
    assert len(args_hash({"a": 1})) == 16


def test_args_hash_empty_dict() -> None:
    """Пустой dict — стабильный, не падает."""
    assert args_hash({}) == args_hash({})


def test_new_event_kinds_registered() -> None:
    """2.5 + 2.10: бюджет, завершение агента и orchestrator.event."""
    from core.events import EventKind

    assert EventKind.BUDGET_WARNING.value == "budget.warning"
    assert EventKind.BUDGET_EXCEEDED.value == "budget.exceeded"
    assert EventKind.AGENT_FINISHED.value == "agent.finished"
    assert EventKind.ORCHESTRATOR_EVENT.value == "orchestrator.event"
