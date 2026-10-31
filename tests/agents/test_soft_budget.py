"""2.10: natural exit + soft budgets."""

from __future__ import annotations

import asyncio
import time
from typing import cast

import pytest

from agents.agent_loop import (
    AgentContext,
    AgentLoop,
    BudgetStatus,
    SoftBudget,
)
from agents.base import LLMBackend
from agents.ollama_client import ChatChunk, TextPart, ToolCall, ToolCallPart
from agents.tool_registry import ToolRegistry, ToolResult
from core.config import AgentMode, ToolPolicy


class _ScriptedClient:
    """Клиент, который проигрывает скрипт чанков и эмитит finish_reason.

    Скрипт — список «ходов». Каждый ход — список чанков + опциональный
    finish_reason для end-чанка.
    """

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self._cancel = False
        self.calls = 0

    @property
    def is_cancelled(self) -> bool:
        return self._cancel

    def cancel_request(self) -> None:
        self._cancel = True

    def reset_cancel(self) -> None:
        self._cancel = False

    async def chat(self, model, messages, tools=None, stream=True):
        if not self.script:
            yield ChatChunk(event_type="end", done=True, finish_reason="stop")
            return
        step = self.script.pop(0)
        self.calls += 1
        yield ChatChunk(event_type="start", done=False)
        for part in step.get("parts", []):
            yield ChatChunk(part=part, event_type="delta", done=False)
        yield ChatChunk(
            event_type="end",
            done=True,
            finish_reason=step.get("finish_reason", "stop"),
        )

    async def embed(self, model, texts):
        return []


class _FakeRegistry:
    def __init__(self, policies: dict[str, ToolPolicy] | None = None):
        self._policies = policies or {}

    def get_schemas(self, mode):
        return [object()]

    def get_policy(self, name):
        return self._policies.get(name, ToolPolicy.AUTO)

    def get_description(self, name):
        return "desc"

    async def execute(self, tool_call, **extra):
        return ToolResult(tool_call.id, tool_call.name, "ok", False)


async def _on_token(_: str) -> None:
    pass


async def _approve(_name, _desc, _args) -> bool:
    return True


# ---------------------------------------------------------------------------
# Natural exit
# ---------------------------------------------------------------------------


class TestNaturalExit:
    async def test_stop_without_tool_calls_exits_immediately(self):
        """finish_reason=stop + нет tool_calls → один вызов, сразу выход."""
        client = _ScriptedClient(
            [{"parts": [TextPart(text="final answer")], "finish_reason": "stop"}]
        )
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        result = await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=[],
            on_token=_on_token,
            on_tool_request=_approve,
            max_iterations=10,
            soft_budget=SoftBudget(iterations=100),
        )
        assert result == "final answer"
        assert client.calls == 1
        assert loop.last_finish_reason == "stop"

    async def test_tool_calls_then_stop(self):
        """tool_call → observation → model даёт stop → выход."""
        tool_call = ToolCall(id="c1", name="read_file", arguments={"path": "a"})
        client = _ScriptedClient(
            [
                {
                    "parts": [ToolCallPart(calls=[tool_call])],
                    "finish_reason": "tool_calls",
                },
                {
                    "parts": [TextPart(text="done")],
                    "finish_reason": "stop",
                },
            ]
        )
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        result = await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=[],
            on_token=_on_token,
            on_tool_request=_approve,
            max_iterations=10,
        )
        assert result == "done"
        assert client.calls == 2


# ---------------------------------------------------------------------------
# Soft budget
# ---------------------------------------------------------------------------


class TestSoftBudget:
    def test_status_ok(self):
        b = SoftBudget(iterations=100, tokens=1000, time_s=100)
        b.reset()
        assert b.status() == BudgetStatus.OK

    def test_warning_at_80_percent(self):
        b = SoftBudget(iterations=10, tokens=1000, time_s=100)
        b.reset()
        b.iteration_count = 8
        assert b.status() == BudgetStatus.WARNING

    def test_exceeded_at_100_percent(self):
        b = SoftBudget(iterations=10, tokens=1000, time_s=100)
        b.reset()
        b.iteration_count = 10
        assert b.status() == BudgetStatus.EXCEEDED

    def test_worst_axis_wins(self):
        """Токены превышены, итерации — нет → EXCEEDED."""
        b = SoftBudget(iterations=100, tokens=10, time_s=100)
        b.reset()
        b.token_count = 15
        assert b.status() == BudgetStatus.EXCEEDED

    def test_snapshot_keys(self):
        b = SoftBudget(iterations=10, tokens=1000, time_s=100)
        b.reset()
        snap = b.snapshot()
        assert set(snap.keys()) == {
            "iterations", "iterations_limit",
            "tokens", "tokens_limit",
            "time_s", "time_limit_s",
            "status",
        }

    async def test_warning_callback_fires_once(self):
        """on_budget_warning срабатывает ровно один раз."""
        calls: list[dict] = []

        async def _on_warn(snapshot: dict) -> None:
            calls.append(snapshot)

        # 2 итерации при iterations=2 → на второй exceeded (не warning).
        # Нужно 1 warning (итерация 1 при iterations=2 не даёт warning,
        # а 8/10 — даёт). Используем tokens: 8 токенов при tokens=10.
        class _TokenReportingClient(_ScriptedClient):
            last_stats = {"tokens": 8, "tokens_per_sec": 1.0}

        client = _TokenReportingClient(
            [
                {"parts": [TextPart(text="a")], "finish_reason": "stop"},
                {"parts": [TextPart(text="b")], "finish_reason": "stop"},
            ]
        )
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=[],
            on_token=_on_token,
            on_tool_request=_approve,
            max_iterations=10,
            soft_budget=SoftBudget(iterations=100, tokens=10, time_s=100),
            on_budget_warning=_on_warn,
        )
        # 1 итерация × 8 токенов = 8/10 → warning.
        assert len(calls) == 1

    async def test_exceeded_chat_forces_final(self):
        """CHAT: exceeded → немедленный финальный синтез без нового tool-цикла."""
        tool_call = ToolCall(id="c1", name="read_file", arguments={"path": "a"})

        class _TokenReportingClient(_ScriptedClient):
            last_stats = {"tokens": 100, "tokens_per_sec": 1.0}

        client = _TokenReportingClient(
            [
                {
                    "parts": [ToolCallPart(calls=[tool_call])],
                    "finish_reason": "tool_calls",
                },
                {
                    "parts": [TextPart(text="forced final")],
                    "finish_reason": "stop",
                },
            ]
        )
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        result = await loop.run(
            context=AgentContext(mode=AgentMode.CHAT),
            messages_history=[],
            on_token=_on_token,
            on_tool_request=_approve,
            max_iterations=10,
            soft_budget=SoftBudget(iterations=100, tokens=10, time_s=100),
        )
        # Один tool-цикл → exceeded → forced final.
        assert result == "forced final"
        assert loop.last_finish_reason == "budget.exceeded"

    async def test_exceeded_agent_continues(self):
        """AGENT: exceeded → цикл идёт дальше до natural stop."""
        calls: list[dict] = []

        async def _on_exceeded(snapshot: dict) -> None:
            calls.append(snapshot)

        tool_call = ToolCall(id="c1", name="read_file", arguments={"path": "a"})

        class _TokenReportingClient(_ScriptedClient):
            last_stats = {"tokens": 100, "tokens_per_sec": 1.0}

        # Две итерации: первая exceeded, вторая — natural stop.
        client = _TokenReportingClient(
            [
                {
                    "parts": [ToolCallPart(calls=[tool_call])],
                    "finish_reason": "tool_calls",
                },
                {
                    "parts": [TextPart(text="continued")],
                    "finish_reason": "stop",
                },
            ]
        )
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        result = await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=[],
            on_token=_on_token,
            on_tool_request=_approve,
            max_iterations=10,
            soft_budget=SoftBudget(iterations=100, tokens=10, time_s=100),
            on_budget_exceeded=_on_exceeded,
        )
        assert result == "continued"
        assert len(calls) == 1  # exceeded сработал один раз
        assert loop.last_finish_reason == "stop"

# ---------------------------------------------------------------------------
# 2.5: budget.warning/exceeded/agent.finished идут в EventBus
# ---------------------------------------------------------------------------


async def test_budget_warning_in_event_bus():
    """При превышении warning-порога — budget.warning в EventBus."""
    from core.event_bus import EventBus, subscribe
    from core.events import EventKind

    EventBus.instance().clear()
    received: list[dict] = []
    unsub = subscribe(
        EventKind.BUDGET_WARNING.value,
        lambda _k, p: received.append(p),
    )

    class _TokenReportingClient(_ScriptedClient):
        last_stats = {"tokens": 8, "tokens_per_sec": 1.0}

    client = _TokenReportingClient(
        [{"parts": [TextPart(text="a")], "finish_reason": "stop"}]
    )
    loop = AgentLoop(
        client=cast(LLMBackend, client),
        registry=cast(ToolRegistry, _FakeRegistry()),
        model="m",
    )
    try:
        await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=[],
            on_token=_on_token,
            on_tool_request=_approve,
            max_iterations=10,
            soft_budget=SoftBudget(iterations=100, tokens=10, time_s=100),
        )
    finally:
        unsub()
    assert len(received) == 1
    assert received[0]["tokens"] == 8
    assert received[0]["tokens_limit"] == 10
    assert received[0]["status"] == "warning"


async def test_agent_finished_in_event_bus():
    """agent.finished эмитится в конце прогона с finish_reason."""
    from core.event_bus import EventBus, subscribe
    from core.events import EventKind

    EventBus.instance().clear()
    received: list[dict] = []
    unsub = subscribe(
        EventKind.AGENT_FINISHED.value,
        lambda _k, p: received.append(p),
    )

    client = _ScriptedClient(
        [{"parts": [TextPart(text="ok")], "finish_reason": "stop"}]
    )
    loop = AgentLoop(
        client=cast(LLMBackend, client),
        registry=cast(ToolRegistry, _FakeRegistry()),
        model="m",
    )
    try:
        await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=[],
            on_token=_on_token,
            on_tool_request=_approve,
            max_iterations=10,
        )
    finally:
        unsub()
    assert len(received) == 1
    assert received[0]["finish_reason"] == "stop"
    assert "budget" in received[0]
    assert received[0]["budget"]["status"] == "ok"
