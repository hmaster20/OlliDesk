"""Тесты колбэков AgentLoop: статусы фаз и результаты инструментов."""

from typing import cast

from agents.agent_loop import AgentContext, AgentLoop, _build_system_prompt
from agents.base import LLMBackend
from agents.ollama_client import ChatChunk, ToolCall
from agents.tool_registry import ToolRegistry, ToolResult
from core.config import AgentMode, ToolPolicy


class _FakeClient:
    """Первый вызов возвращает tool_call, второй — финальный текст."""

    def __init__(self):
        self.calls = 0

    @property
    def is_cancelled(self) -> bool:
        return False

    async def chat(self, model, messages, tools=None, stream=True):
        self.calls += 1
        if self.calls == 1:
            yield ChatChunk(
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"})],
                done=True,
            )
        else:
            yield ChatChunk(content="Готово", done=True)


class _FakeRegistry:
    def get_schemas(self, mode):
        return [object()]

    def get_policy(self, name):
        return ToolPolicy.AUTO

    def get_description(self, name):
        return "описание"

    async def execute(self, tool_call, **extra):
        return ToolResult(tool_call.id, tool_call.name, "содержимое файла", False)


async def _approve(name, description, arguments):
    return True


class TestAgentLoopCallbacks:
    async def test_status_emitted_per_iteration(self):
        statuses: list[str] = []
        tokens: list[str] = []

        async def on_token(t: str):
            tokens.append(t)

        async def on_status(s: str):
            statuses.append(s)

        loop = AgentLoop(
            client=cast(LLMBackend, _FakeClient()),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        result = await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=[],
            on_token=on_token,
            on_tool_request=_approve,
            max_iterations=3,
            on_status=on_status,
        )

        assert result == "Готово"
        assert statuses == [
            "🤔 Анализирую запрос…",
            "🔧 Подготовка вызовов: read_file",
            "🧠 Размышляю над результатами (шаг 2/3)…",
        ]
        assert tokens == ["Готово"]

    async def test_tool_result_callback(self):
        results: list[tuple[str, str, bool]] = []

        async def on_token(t: str):
            pass

        async def on_tool_result(name: str, content: str, is_error: bool):
            results.append((name, content, is_error))

        loop = AgentLoop(
            client=cast(LLMBackend, _FakeClient()),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=[],
            on_token=on_token,
            on_tool_request=_approve,
            max_iterations=3,
            on_tool_result=on_tool_result,
        )

        assert results == [("read_file", "содержимое файла", False)]


class _LoopingClient:
    """Всегда возвращает один и тот же tool_call (имитация зацикливания)."""

    def __init__(self):
        self.calls = 0

    @property
    def is_cancelled(self) -> bool:
        return False

    async def chat(self, model, messages, tools=None, stream=True):
        self.calls += 1
        yield ChatChunk(
            tool_calls=[ToolCall(id=f"c{self.calls}", name="read_file", arguments={"path": "a.txt"})],
            done=True,
        )


class TestAgentLoopGuard:
    async def test_repeated_tool_call_stops_early(self):
        client = _LoopingClient()
        statuses: list[str] = []

        async def on_token(t: str):
            pass

        async def on_status(s: str):
            statuses.append(s)

        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        result = await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=[],
            on_token=on_token,
            on_tool_request=_approve,
            max_iterations=30,
            on_status=on_status,
        )

        assert "повторяющиеся вызовы" in result
        assert client.calls == 2
        assert "⏹ Обнаружено повторение вызовов — завершаю" in statuses


class TestSystemPromptProjectMap:
    """Карта проекта попадает в системный промпт."""

    def test_project_map_injected(self):
        prompt = _build_system_prompt(AgentContext(project_map="Структура проекта:\ncloud/"))

        assert "Структура проекта:" in prompt
        assert "cloud/" in prompt

    def test_empty_project_map_omitted(self):
        prompt = _build_system_prompt(AgentContext())

        assert "Структура проекта:" not in prompt
