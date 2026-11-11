"""10.4: verifier срабатывает только в PLAN/AGENT и research-в-CHAT."""

from __future__ import annotations

from typing import cast

import pytest

from agents.agent_loop import AgentContext, AgentLoop
from agents.base import LLMBackend
from agents.ollama_client import ChatChunk, TextPart
from agents.tool_registry import ToolRegistry, ToolResult
from core.config import AgentMode, ToolPolicy


class _NoopClient:
    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    @property
    def is_cancelled(self):
        return False

    async def chat(self, model, messages, tools=None, stream=True):
        self.calls += 1
        yield ChatChunk(part=TextPart(text=self._text), event_type="delta", done=False)
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


def _project_map() -> str:
    return "Структура проекта:\n  src/\n    main.py\n  tests/\n    test_main.py\n"


class TestVerifierCondition:
    @pytest.mark.asyncio
    async def test_chat_non_research_skips_verifier(self) -> None:
        """CHAT без research-вопроса — verifier не запускается (нет регенерации)."""
        # Ответ содержит несуществующий путь — но в CHAT verifier не должен сработать.
        text = "Это стихотворение про файл ghost/missing.py и класс FooBarBaz."
        client = _NoopClient(text)
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        ctx = AgentContext(
            mode=AgentMode.CHAT,
            project_map=_project_map(),
        )
        await loop.run(
            context=ctx,
            messages_history=[],
            on_token=_noop,
            on_tool_request=_approve,
            max_iterations=2,
        )
        # Только один вызов — регенерации не было.
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_agent_triggers_verifier(self) -> None:
        """AGENT с невалидным путём — verifier → регенерация."""
        client = _NoopClient("В файле ghost/missing.py есть функция FooBarBaz.")
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        ctx = AgentContext(
            mode=AgentMode.AGENT,
            project_map=_project_map(),
        )
        # Первый ответ содержит ghost — verifier должен запустить регенерацию.
        # Второй ответ (тот же клиент отдаст тот же текст) → вернётся с пометкой.
        await loop.run(
            context=ctx,
            messages_history=[],
            on_token=_noop,
            on_tool_request=_approve,
            max_iterations=2,
        )
        # Хотя бы один дополнительный вызов (регенерация).
        assert client.calls >= 2
