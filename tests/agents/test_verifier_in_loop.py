"""10.4: verifier в AgentLoop — pass / fail+regen / fail+2."""

from __future__ import annotations

from typing import cast

import pytest

from agents.agent_loop import AgentContext, AgentLoop
from agents.base import LLMBackend
from agents.ollama_client import ChatChunk, TextPart
from agents.tool_registry import ToolRegistry, ToolResult
from core.config import AgentMode, ToolPolicy


class _ScriptedClient:
    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls = 0
        self.seen_system: list[str] = []

    @property
    def is_cancelled(self):
        return False

    async def chat(self, model, messages, tools=None, stream=True):
        if messages and messages[0].role == "system":
            self.seen_system.append(messages[0].content)
        self.calls += 1
        text = self.texts.pop(0) if self.texts else ""
        yield ChatChunk(part=TextPart(text=text), event_type="delta", done=False)
        yield ChatChunk(event_type="end", done=True, finish_reason="stop")

    async def embed(self, model, texts):
        return []


class _FakeRegistry:
    def get_schemas(self, mode):
        return []

    def get_policy(self, name):
        return ToolPolicy.AUTO

    def get_description(self, name):
        return ""

    async def execute(self, tool_call, **extra):
        return ToolResult(tool_call.id, tool_call.name, "", False)


async def _noop(_: str) -> None:
    pass


async def _approve(_, __, ___):
    return True


class TestVerifierInLoop:
    @pytest.mark.asyncio
    async def test_no_verify_without_project_map(self) -> None:
        client = _ScriptedClient(["final answer"])
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        # project_map пуст → verifier не запускается.
        result = await loop.run(
            context=AgentContext(mode=AgentMode.CHAT),
            messages_history=[],
            on_token=_noop,
            on_tool_request=_approve,
            max_iterations=3,
        )
        assert result == "final answer"
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_regen_on_failure(self) -> None:
        """Первый ответ содержит невалидный путь — verifier регенерирует."""
        client = _ScriptedClient(
            [
                "В файле ghost/missing.py есть функция Foo.",  # содержит ghost
                "Ответ без вымышленных путей.",  # второй ответ
            ]
        )
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        ctx = AgentContext(
            mode=AgentMode.CHAT,
            project_map=("Структура проекта:\n  src/\n    main.py\n  tests/\n    test_main.py\n"),
        )
        result = await loop.run(
            context=ctx,
            messages_history=[],
            on_token=_noop,
            on_tool_request=_approve,
            max_iterations=3,
        )
        # Регенерация прошла.
        assert client.calls == 2
        assert "ghost" not in result
