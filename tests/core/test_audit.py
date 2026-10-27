"""0.1.1: AuditLog — запись/чтение вызовов инструментов."""

from __future__ import annotations

import pytest

from core.audit import AuditLog, _hash


@pytest.fixture
def log(tmp_path):
    return AuditLog(db_path=tmp_path / "audit.db")


def test_log_records_call(log):
    log.log(
        "run_shell_command",
        {"command": "Test-Path .", "cwd": ""},
        policy="ask_first",
        approved=True,
        result_content="True",
        is_error=False,
        dangerous=False,
    )
    assert log.count() == 1
    entry = log.get_recent(limit=1)[0]
    assert entry.tool == "run_shell_command"
    assert entry.policy == "ask_first"
    assert entry.approved is True
    assert entry.dangerous is False
    assert entry.error == ""


def test_hash_stable_for_same_args(log):
    log.log("t", {"a": 1, "b": 2}, approved=True, dangerous=False)
    log.log("t", {"b": 2, "a": 1}, approved=True, dangerous=False)  # другой порядок
    entries = log.get_recent(limit=2)
    assert entries[0].args_hash == entries[1].args_hash


def test_hash_differs_for_different_args(log):
    log.log("t", {"a": 1}, approved=True, dangerous=False)
    log.log("t", {"a": 2}, approved=True, dangerous=False)
    entries = log.get_recent(limit=2)
    assert entries[0].args_hash != entries[1].args_hash


def test_hash_handles_non_serializable():
    """Хеш не падает на нестандартных объектах (default=str)."""
    h1 = _hash({"x": object()})
    h2 = _hash({"x": object()})
    assert isinstance(h1, str)
    assert len(h1) == 32
    # str(object()) содержит адрес — хеши разные, но оба валидные
    assert isinstance(h2, str)
    assert len(h2) == 32


def test_log_error_stored(log):
    log.log(
        "run_shell_command",
        {"command": "rm -rf /"},
        approved=False,
        result_content="blacklist",
        is_error=True,
        dangerous=True,
    )
    entry = log.get_recent(limit=1)[0]
    assert entry.approved is False
    assert entry.error == "error"
    assert entry.dangerous is True


def test_get_recent_returns_descending(log):
    for i in range(5):
        log.log("t", {"i": i}, approved=True, dangerous=False)
    entries = log.get_recent(limit=3)
    assert len(entries) == 3
    ids = [e.id for e in entries]
    assert ids == sorted(ids, reverse=True)