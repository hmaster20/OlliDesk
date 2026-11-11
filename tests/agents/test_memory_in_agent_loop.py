"""4.3: MemoryService в AgentLoop — recall в system-промпт."""

from __future__ import annotations

from typing import cast

from agents.agent_loop import AgentContext, AgentLoop, _build_system_prompt
from agents.base import LLMBackend
from agents.ollama_client import ChatChunk, TextPart
from agents.tool_registry import ToolRegistry, ToolResult
from core.config import AgentMode, ToolPolicy
from core.services.memory_service import MemoryService, Reflection


class _NoopClient:
    def __init__(self):
        self.seen_system: list[str] = []

    @property
    def is_cancelled(self):
        return False

    async def chat(self, model, messages, tools=None, stream=True):
        # Сохраняем system-промпт для проверки.
        if messages and messages[0].role == "system":
            self.seen_system.append(messages[0].content)
        yield ChatChunk(part=TextPart(text="ok"), event_type="delta", done=False)
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


async def _on_token(_: str) -> None:
    pass


async def _approve(_, __, ___):
    return True


class TestMemoryInPrompt:
    def test_memory_block_injected(self, tmp_path):
        svc = MemoryService(tmp_path / "m.db")
        try:
            svc.remember(
                Reflection(
                    id=None,
                    created_at="",
                    project_path="myproj",
                    task_type="code",
                    summary="fixed parser bug",
                    outcome="success",
                    tags=[],
                )
            )
            ctx = AgentContext(
                project_name="myproj",
                project_root="myproj",
                memory_service=svc,
            )
            prompt = _build_system_prompt(ctx)
            assert "Память (прошлые задачи" in prompt
            assert "fixed parser bug" in prompt
        finally:
            svc.close()

    def test_no_memory_when_service_none(self):
        ctx = AgentContext(project_name="myproj")
        prompt = _build_system_prompt(ctx)
        assert "Память (прошлые задачи" not in prompt

    def test_no_memory_when_query_empty(self, tmp_path):
        """Без project_name/extra_rules recall не вызывается."""
        svc = MemoryService(tmp_path / "m.db")
        try:
            svc.remember(
                Reflection(
                    id=None,
                    created_at="",
                    project_path="",
                    task_type="code",
                    summary="something",
                    outcome="success",
                    tags=[],
                )
            )
            ctx = AgentContext(memory_service=svc)
            prompt = _build_system_prompt(ctx)
            # recall не вызывается — нет query.
            assert "Память (прошлые задачи" not in prompt
        finally:
            svc.close()


class TestMemoryInAgentLoopRun:
    async def test_agent_loop_passes_memory_to_prompt(self, tmp_path):
        svc = MemoryService(tmp_path / "m.db")
        try:
            svc.remember(
                Reflection(
                    id=None,
                    created_at="",
                    project_path="proj",
                    task_type="code",
                    summary="important past work",
                    outcome="success",
                    tags=[],
                )
            )
            client = _NoopClient()
            loop = AgentLoop(
                client=cast(LLMBackend, client),
                registry=cast(ToolRegistry, _FakeRegistry()),
                model="m",
            )
            ctx = AgentContext(
                project_name="proj",
                project_root="proj",
                mode=AgentMode.CHAT,
                memory_service=svc,
            )
            await loop.run(
                context=ctx,
                messages_history=[],
                on_token=_on_token,
                on_tool_request=_approve,
                max_iterations=3,
            )
            # System-промпт содержал блок памяти.
            assert client.seen_system
            assert "important past work" in client.seen_system[0]
        finally:
            svc.close()
