"""Тесты реестра инструментов."""

from typing import Any, cast

import pytest

from agents.ollama_client import ToolCall
from agents.tool_registry import ToolRegistry, tool
from core.config import AgentMode, ToolPolicy


@tool(
    name="probe_tool",
    description="Пробный инструмент",
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.AGENT],
)
def probe_tool(
    query: str,
    limit: int = 5,
    project_root: Any = None,
    vector_store: Any = None,
    search_cache: Any = None,
    attached_files: Any = None,
) -> str:
    """Первая строка докстринга попадёт в описание."""
    return "ok"


def _schema_props(registry: ToolRegistry, mode: AgentMode = AgentMode.CHAT) -> dict[str, Any]:
    return cast(dict[str, Any], registry.get_schemas(mode)[0].function["parameters"])


class TestSchemaGeneration:
    """Генерация JSON-схемы из сигнатуры функции."""

    def test_injected_params_hidden(self):
        props = _schema_props(_registry_with(probe_tool))
        assert set(props["properties"]) == {"query", "limit"}

    def test_required_only_for_params_without_default(self):
        params = _schema_props(_registry_with(probe_tool))
        assert params["required"] == ["query"]

    def test_description_from_docstring(self):
        assert _registry_with(probe_tool).get_description("probe_tool") == "Пробный инструмент"

    def test_unknown_tool_description(self):
        assert ToolRegistry().get_description("nope") == ""

    def test_undecorated_function_rejected(self):
        def plain(x: str) -> str:
            return x

        with pytest.raises(ValueError, match="не имеет декоратора"):
            ToolRegistry().register(plain)


class TestPolicies:
    """Политики выполнения."""

    def test_mode_filtering(self):
        registry = _registry_with(probe_tool)
        assert len(registry.get_schemas(AgentMode.CHAT)) == 1
        assert len(registry.get_schemas(AgentMode.AGENT)) == 1
        assert registry.get_schemas(AgentMode.PLAN) == []

    def test_excluded_policy_hides_tool(self):
        registry = _registry_with(probe_tool)
        registry.set_policy("probe_tool", ToolPolicy.EXCLUDED)
        assert registry.get_schemas(AgentMode.CHAT) == []
        assert registry.get_policy("probe_tool") == ToolPolicy.EXCLUDED

    def test_default_policy_ask_first(self):
        @tool(name="asker", description="asker")
        def asker() -> str:
            return "ok"

        assert _registry_with(asker).get_policy("asker") == ToolPolicy.ASK_FIRST

    def test_unknown_tool_policy(self):
        assert ToolRegistry().get_policy("nope") == ToolPolicy.EXCLUDED


class TestExecute:
    """Исполнение инструментов."""

    @pytest.mark.asyncio
    async def test_injected_kwargs_are_passed(self):
        received: dict[str, Any] = {}

        @tool(name="capture", description="capture", policy=ToolPolicy.AUTO)
        def capture(query: str, attached_files: Any = None) -> str:
            nonlocal received
            received = {"query": query, "attached_files": attached_files}
            return "ok"

        registry = _registry_with(capture)
        store = object()

        result = await registry.execute(
            ToolCall(name="capture", arguments={"query": "x"}),
            attached_files=store,
        )

        assert result.is_error is False
        assert result.content == "ok"
        assert received == {"query": "x", "attached_files": store}

    @pytest.mark.asyncio
    async def test_async_tool_supported(self):
        @tool(name="afetch", description="afetch", policy=ToolPolicy.AUTO)
        async def afetch(query: str) -> str:
            return f"got {query}"

        result = await _registry_with(afetch).execute(ToolCall(name="afetch", arguments={"query": "y"}))
        assert result.content == "got y"

    @pytest.mark.asyncio
    async def test_unknown_extra_kwargs_ignored(self):
        @tool(name="bare", description="bare", policy=ToolPolicy.AUTO)
        def bare() -> str:
            return "ok"

        result = await _registry_with(bare).execute(
            ToolCall(name="bare", arguments={}), search_cache=object()
        )
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        result = await ToolRegistry().execute(ToolCall(name="nope", arguments={}))
        assert result.is_error
        assert "не найден" in result.content

    @pytest.mark.asyncio
    async def test_exception_becomes_error_result(self):
        @tool(name="boom", description="boom", policy=ToolPolicy.AUTO)
        def boom() -> str:
            raise RuntimeError("kaput")

        result = await _registry_with(boom).execute(ToolCall(name="boom", arguments={}))
        assert result.is_error
        assert "kaput" in result.content


def _registry_with(*funcs: Any) -> ToolRegistry:
    registry = ToolRegistry()
    for func in funcs:
        registry.register(func)
    return registry
