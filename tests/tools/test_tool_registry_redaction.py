"""Тесты 0.1.2: redaction в ToolRegistry.execute."""

from __future__ import annotations

import pytest

from agents.tool_registry import ToolRegistry, tool
from core.config import AgentMode, ToolPolicy


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()

    @tool(
        name="leak_secret",
        description="returns secret in content",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT],
    )
    def leak_secret() -> str:
        return "PASSWORD=hunter2hunter2 in output"

    @tool(
        name="leak_in_dict",
        description="returns dict with _llm_text and metadata",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT],
    )
    def leak_in_dict() -> dict:
        return {
            "_llm_text": "TOKEN=abcdef123456 in text",
            "diff": "--- PASSWORD=hunter2hunter2 (metadata — не трогаем)",
            "lines_added": 1,
        }

    reg.register(leak_secret)
    reg.register(leak_in_dict)
    return reg


@pytest.mark.asyncio
async def test_str_result_is_redacted(registry: ToolRegistry) -> None:
    from agents.ollama_client import ToolCall

    result = await registry.execute(ToolCall(name="leak_secret", arguments={}))
    assert "hunter2hunter2" not in result.content
    assert "[REDACTED:env_secret]" in result.content


@pytest.mark.asyncio
async def test_dict_result_llm_text_is_redacted(registry: ToolRegistry) -> None:
    from agents.ollama_client import ToolCall

    result = await registry.execute(ToolCall(name="leak_in_dict", arguments={}))
    assert "abcdef123456" not in result.content
    assert "[REDACTED:env_secret]" in result.content
    # metadata не трогается — это пользовательский diff.
    assert result.metadata is not None
    assert "hunter2hunter2" in result.metadata["diff"]


@pytest.mark.asyncio
async def test_error_arguments_are_redacted() -> None:
    from agents.ollama_client import ToolCall

    reg = ToolRegistry()

    @tool(
        name="needs_int",
        description="takes int",
        policy=ToolPolicy.AUTO,
        modes=[AgentMode.CHAT],
    )
    def needs_int(value: int) -> str:
        return str(value)

    reg.register(needs_int)
    # Передаём невалидный value с секретом в строке — pydantic покажет его в ошибке.
    result = await reg.execute(
        ToolCall(name="needs_int", arguments={"value": "PASSWORD=hunter2hunter2"}),
    )
    assert result.is_error
    assert "hunter2hunter2" not in result.content