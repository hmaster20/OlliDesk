"""Тесты ErrorCategory в ToolRegistry.execute.

Проверяем, что исключения инструментов мапятся на правильную категорию —
от этого зависит, какую подсказку получит модель в Observation
(см. agents/agent_loop.py::_RETRY_HINTS).
"""

from __future__ import annotations

import httpx
import pytest

from agents.ollama_client import ToolCall
from agents.tool_registry import ErrorCategory, ToolRegistry, ToolResult, tool
from core.config import AgentMode, ToolPolicy


@pytest.fixture
def registry() -> ToolRegistry:
    """Свежий реестр с инструментами, падающими разными способами."""
    r = ToolRegistry()

    @tool(
        name="ok_tool",
        description="ok",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
    )
    def ok_tool(x: int = 0) -> str:
        return "ok"

    @tool(
        name="file_not_found_tool",
        description="raises FileNotFoundError",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
    )
    def file_not_found_tool() -> str:
        raise FileNotFoundError("no such file")

    @tool(
        name="network_error_tool",
        description="raises ConnectError",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
    )
    def network_error_tool() -> str:
        raise httpx.ConnectError("connection refused")

    @tool(
        name="generic_error_tool",
        description="raises ValueError",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
    )
    def generic_error_tool() -> str:
        raise ValueError("boom")

    @tool(
        name="validator_tool",
        description="requires int argument",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
    )
    def validator_tool(n: int) -> int:
        return n

    for f in (
        ok_tool,
        file_not_found_tool,
        network_error_tool,
        generic_error_tool,
        validator_tool,
    ):
        r.register(f)
    return r


class TestErrorCategories:
    async def test_ok_returns_no_category(self, registry: ToolRegistry) -> None:
        tc = ToolCall(name="ok_tool", arguments={})
        result = await registry.execute(tc)
        assert not result.is_error
        assert result.error_category is None

    async def test_unknown_tool_fix_arguments(self, registry: ToolRegistry) -> None:
        tc = ToolCall(name="nonexistent", arguments={})
        result = await registry.execute(tc)
        assert result.is_error
        assert result.error_category == ErrorCategory.FIX_ARGUMENTS

    async def test_validation_error_fix_arguments(self, registry: ToolRegistry) -> None:
        # Строка "abc" не конвертируется в int → ValidationError в pydantic.
        tc = ToolCall(name="validator_tool", arguments={"n": "abc"})
        result = await registry.execute(tc)
        assert result.is_error
        assert result.error_category == ErrorCategory.FIX_ARGUMENTS

    async def test_missing_required_argument_fix_arguments(
        self, registry: ToolRegistry
    ) -> None:
        # Пропущен обязательный аргумент n.
        tc = ToolCall(name="validator_tool", arguments={})
        result = await registry.execute(tc)
        assert result.is_error
        assert result.error_category == ErrorCategory.FIX_ARGUMENTS

    async def test_file_not_found_different_path(self, registry: ToolRegistry) -> None:
        tc = ToolCall(name="file_not_found_tool", arguments={})
        result = await registry.execute(tc)
        assert result.is_error
        assert result.error_category == ErrorCategory.DIFFERENT_PATH

    async def test_connect_error_retry_later(self, registry: ToolRegistry) -> None:
        tc = ToolCall(name="network_error_tool", arguments={})
        result = await registry.execute(tc)
        assert result.is_error
        assert result.error_category == ErrorCategory.RETRY_LATER

    async def test_generic_error_report_to_user(self, registry: ToolRegistry) -> None:
        tc = ToolCall(name="generic_error_tool", arguments={})
        result = await registry.execute(tc)
        assert result.is_error
        assert result.error_category == ErrorCategory.REPORT_TO_USER


class TestToolResultDefault:
    """Старые вызовы ToolResult без error_category не ломаются."""

    def test_default_category_is_none(self) -> None:
        r = ToolResult(tool_call_id="x", name="y", content="z")
        assert r.is_error is False
        assert r.error_category is None

    def test_explicit_category(self) -> None:
        r = ToolResult(
            tool_call_id="x",
            name="y",
            content="z",
            is_error=True,
            error_category=ErrorCategory.DIFFERENT_PATH,
        )
        assert r.is_error is True
        assert r.error_category == ErrorCategory.DIFFERENT_PATH


class TestToolMetadata:
    """is_mutating/category — новые поля декоратора @tool."""

    def test_read_file_defaults(self) -> None:
        from tools.file_ops import read_file

        meta = read_file._tool_meta  # type: ignore[attr-defined]
        assert meta["is_mutating"] is False
        assert meta["category"] == "filesystem"

    def test_write_file_is_mutating(self) -> None:
        from tools.file_ops import write_file

        meta = write_file._tool_meta  # type: ignore[attr-defined]
        assert meta["is_mutating"] is True
        assert meta["category"] == "filesystem"

    def test_shell_is_shell_category(self) -> None:
        from tools.shell_ops import run_shell_command

        meta = run_shell_command._tool_meta  # type: ignore[attr-defined]
        assert meta["category"] == "shell"
        assert meta["is_mutating"] is True