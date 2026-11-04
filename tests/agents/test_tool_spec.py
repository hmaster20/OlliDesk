"""Тесты 2.6: ToolSpec v2, capabilities, timeout_s."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from agents.ollama_client import ToolCall
from agents.tool_registry import (
    _DEFAULT_CAPABILITIES,
    _VALID_CAPABILITIES,
    ToolRegistry,
    ToolSpec,
    tool,
)
from core.config import AgentMode, ToolPolicy

# ---------------------------------------------------------------------------
# ToolSpec / @tool
# ---------------------------------------------------------------------------


def test_toolspec_is_frozen() -> None:
    spec = ToolSpec(
        name="x",
        description="",
        schema={},
        modes=frozenset({AgentMode.CHAT}),
        policy=ToolPolicy.AUTO,
        capabilities=frozenset({"fs.read"}),
        is_mutating=False,
        category="filesystem",
    )
    with pytest.raises(FrozenInstanceError):
        spec.name = "y"  # type: ignore[misc]


def test_tool_default_capabilities() -> None:
    reg = ToolRegistry()

    @tool(name="t", description="", policy=ToolPolicy.AUTO, modes=[AgentMode.CHAT])
    def t() -> str:
        return "ok"

    reg.register(t)
    assert reg.get_spec("t").capabilities == _DEFAULT_CAPABILITIES


def test_tool_explicit_capabilities() -> None:
    reg = ToolRegistry()

    @tool(
        name="t",
        description="",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT],
        capabilities=["fs.write", "net"],
    )
    def t() -> str:
        return "ok"

    reg.register(t)
    assert reg.get_spec("t").capabilities == frozenset({"fs.write", "net"})


def test_tool_empty_capabilities_allowed() -> None:
    reg = ToolRegistry()

    @tool(
        name="t",
        description="",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT],
        capabilities=[],
    )
    def t() -> str:
        return "ok"

    reg.register(t)
    assert reg.get_spec("t").capabilities == frozenset()


def test_tool_unknown_capability_raises() -> None:
    with pytest.raises(ValueError, match="Неизвестные capabilities"):
        @tool(
            name="t",
            description="",
            policy=ToolPolicy.AUTO,
            modes=[AgentMode.CHAT],
            capabilities=["fs.explode"],
        )
        def t() -> str:
            return "ok"


def test_tool_negative_timeout_raises() -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        @tool(
            name="t",
            description="",
            policy=ToolPolicy.AUTO,
            modes=[AgentMode.CHAT],
            timeout_s=-1,
        )
        def t() -> str:
            return "ok"


def test_tool_negative_max_output_chars_raises() -> None:
    with pytest.raises(ValueError, match="max_output_chars"):
        @tool(
            name="t",
            description="",
            policy=ToolPolicy.AUTO,
            modes=[AgentMode.CHAT],
            max_output_chars=-1,
        )
        def t() -> str:
            return "ok"


# ---------------------------------------------------------------------------
# get_spec / try_get_spec / get_all_specs
# ---------------------------------------------------------------------------


@pytest.fixture
def reg() -> ToolRegistry:
    r = ToolRegistry()

    @tool(name="alpha", description="", policy=ToolPolicy.AUTO, modes=[AgentMode.CHAT])
    def alpha() -> str:
        return "a"

    @tool(name="beta", description="", policy=ToolPolicy.ASK_FIRST, modes=[AgentMode.AGENT])
    def beta() -> str:
        return "b"

    r.register(alpha)
    r.register(beta)
    return r


def test_get_spec_returns_toolspec(reg: ToolRegistry) -> None:
    spec = reg.get_spec("alpha")
    assert isinstance(spec, ToolSpec)
    assert spec.name == "alpha"


def test_get_spec_unknown_raises(reg: ToolRegistry) -> None:
    with pytest.raises(KeyError, match="not registered|не зарегистрирован"):
        reg.get_spec("missing")


def test_try_get_spec_none_for_missing(reg: ToolRegistry) -> None:
    assert reg.try_get_spec("missing") is None


def test_try_get_spec_returns_spec(reg: ToolRegistry) -> None:
    spec = reg.try_get_spec("alpha")
    assert spec is not None
    assert spec.name == "alpha"


def test_get_all_specs(reg: ToolRegistry) -> None:
    specs = reg.get_all_specs()
    assert {s.name for s in specs} == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# timeout_s: реально применяется
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_timeout_enforced() -> None:
    reg = ToolRegistry()

    @tool(
        name="slow",
        description="",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT],
        timeout_s=0.05,
    )
    async def slow() -> str:
        await asyncio.sleep(1.0)
        return "done"

    reg.register(slow)
    result = await reg.execute(ToolCall(name="slow", arguments={}))
    assert result.is_error
    assert "таймаут" in result.content.lower()


@pytest.mark.asyncio
async def test_sync_timeout_enforced() -> None:
    import time

    reg = ToolRegistry()

    @tool(
        name="slow_sync",
        description="",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT],
        timeout_s=0.05,
    )
    def slow_sync() -> str:
        time.sleep(1.0)
        return "done"

    reg.register(slow_sync)
    result = await reg.execute(ToolCall(name="slow_sync", arguments={}))
    assert result.is_error
    assert "таймаут" in result.content.lower()


@pytest.mark.asyncio
async def test_timeout_zero_means_no_limit() -> None:
    reg = ToolRegistry()

    @tool(
        name="fast",
        description="",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT],
        timeout_s=0,
    )
    async def fast() -> str:
        return "ok"

    reg.register(fast)
    result = await reg.execute(ToolCall(name="fast", arguments={}))
    assert not result.is_error


# ---------------------------------------------------------------------------
# Все реальные инструменты — capabilities ⊆ _VALID_CAPABILITIES
# ---------------------------------------------------------------------------


def test_all_registered_tools_have_valid_specs() -> None:
    """Инструменты из ALL_TOOLS: валидные capabilities и timeout_s >= 0."""
    from tools.registry import ALL_TOOLS

    reg = ToolRegistry()
    for tool_func in ALL_TOOLS:
        reg.register(tool_func)

    for spec in reg.get_all_specs():
        assert spec.capabilities <= _VALID_CAPABILITIES, (
            f"{spec.name}: недопустимые capabilities "
            f"{spec.capabilities - _VALID_CAPABILITIES}"
        )
        assert spec.timeout_s >= 0
        assert spec.max_output_chars >= 0


# ---------------------------------------------------------------------------
# Назначение capabilities у конкретных инструментов
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "expected_caps"),
    [
        ("read_file", {"fs.read"}),
        ("list_directory", {"fs.read"}),
        ("write_file", {"fs.write"}),
        ("search_codebase", {"fs.read"}),
        ("web_search", {"net"}),
        ("get_weather", {"net"}),
        ("get_exchange_rate", {"net"}),
        ("get_exchange_rates_multi", {"net"}),
        ("get_current_datetime", frozenset()),
        ("get_git_status", {"git"}),
        ("create_snapshot", {"git"}),
        ("undo_last_snapshot", {"git"}),
        ("read_attached_file", {"fs.read"}),
        ("record_hypothesis", {"ui"}),
        ("update_hypothesis", {"ui"}),
        ("list_hypotheses", {"ui"}),
        ("run_shell_command", {"shell", "fs.write"}),
    ],
)
def test_capabilities_per_tool(tool_name: str, expected_caps: set[str] | frozenset[str]) -> None:
    """Каждый инструмент имеет ровно ту capability, которую ожидает Policy Engine."""
    from tools.registry import ALL_TOOLS

    reg = ToolRegistry()
    for tool_func in ALL_TOOLS:
        reg.register(tool_func)

    spec = reg.get_spec(tool_name)
    assert spec.capabilities == frozenset(expected_caps), (
        f"{tool_name}: ожидалось {frozenset(expected_caps)}, "
        f"получено {spec.capabilities}"
    )