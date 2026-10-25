"""Тесты колбэков AgentLoop: статусы фаз и результаты инструментов."""

from typing import cast

from agents.agent_loop import AgentContext, AgentLoop, _build_system_prompt
from agents.base import LLMBackend
from agents.ollama_client import ChatChunk, ToolCall
from agents.tool_registry import ToolRegistry, ToolResult
from core.config import AgentMode, ToolPolicy
from core.context_budget import system_prompt_char_budget


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
        # [4.8.E] Сигнатура 4.x: metadata передаётся 4-м аргументом (dict | None).
        results: list[tuple[str, str, bool]] = []

        async def on_token(t: str):
            pass

        async def on_tool_result(
            name: str,
            content: str,
            is_error: bool,
            metadata: dict | None = None,
        ):
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
        """Повторный tool_call с теми же аргументами → принудительный финальный синтез.

        Клиент вызывается 3 раза: 2 tool-итерации + 1 финальный вызов
        без tools (обязательный выход из цикла по инварианту AgentLoop).
        """
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
        assert client.calls == 3
        # Последний статус — «формирую итоговый ответ».
        assert "повторные вызовы без прогресса" in statuses[-1]


class _FakeHallucinationClient:
    """Первый ответ — фейковая ReAct-разметка без распознаваемого вызова."""

    def __init__(self):
        self.calls = 0
        self.seen: list[list] = []

    @property
    def is_cancelled(self):
        return False

    async def chat(self, model, messages, tools=None, stream=True):
        self.calls += 1
        self.seen.append(list(messages))
        if self.calls == 1:
            yield ChatChunk(
                content="## Observation\nВ проекте есть файл Program.cs с классом Main.",
                done=True,
            )
        else:
            yield ChatChunk(content="Итоговый ответ по проекту.", done=True)


class _FakePlainClient:
    """Обычный ответ без следов вызова инструмента."""

    def __init__(self):
        self.calls = 0

    @property
    def is_cancelled(self):
        return False

    async def chat(self, model, messages, tools=None, stream=True):
        self.calls += 1
        yield ChatChunk(content="Привет! Чем помочь?", done=True)


class _FakeAlwaysTraceClient:
    """Каждый ответ — фейковая ReAct-разметка без распознаваемого вызова."""

    def __init__(self):
        self.calls = 0

    @property
    def is_cancelled(self):
        return False

    async def chat(self, model, messages, tools=None, stream=True):
        self.calls += 1
        yield ChatChunk(content="## Action\nосмотреть проект", done=True)


class TestAntiHallucinationGuard:
    async def test_fake_trace_triggers_retry(self):
        client = _FakeHallucinationClient()
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
            max_iterations=5,
            on_status=on_status,
        )

        assert result == "Итоговый ответ по проекту."
        assert client.calls == 2
        assert "🔁 Формат вызова не распознан — уточняю…" in statuses
        # Во втором запросе модели показали корректирующую подсказку.
        hint_seen = any(
            "не был распознан" in str(getattr(m, "content", ""))
            for m in client.seen[1]
        )
        assert hint_seen

    async def test_plain_answer_not_nudged(self):
        client = _FakePlainClient()

        async def on_token(t: str):
            pass

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
            max_iterations=5,
        )

        assert result == "Привет! Чем помочь?"
        assert client.calls == 1

    async def test_guard_fires_only_once(self):
        client = _FakeAlwaysTraceClient()

        async def on_token(t: str):
            pass

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
            max_iterations=5,
        )

        assert "## Action" in result
        assert client.calls == 2


class TestSystemPromptProjectMap:
    """Карта проекта попадает в системный промпт."""

    def test_project_map_injected(self):
        prompt = _build_system_prompt(AgentContext(project_map="Структура проекта:\ncloud/"))

        assert "Структура проекта:" in prompt
        assert "cloud/" in prompt

    def test_empty_project_map_omitted(self):
        prompt = _build_system_prompt(AgentContext())

        assert "Структура проекта:" not in prompt


class TestSystemPromptLanguage:
    """Языковая директива всегда в конце системного промпта."""

    def test_language_directive_present(self):
        prompt = _build_system_prompt(AgentContext())

        assert "ЯЗЫК ОТВЕТА" in prompt
        # Директива — последний блок: модели сильнее следуют поздним инструкциям.
        # Текст директивы: «...есть общепринятый русский аналог.»
        assert prompt.rstrip().endswith("русский аналог.")

    def test_language_directive_after_project_map(self):
        prompt = _build_system_prompt(AgentContext(project_map="Структура проекта:\ncloud/"))

        assert prompt.index("ЯЗЫК ОТВЕТА") > prompt.index("cloud/")


class TestSystemPromptProjectName:
    """Имя и корень проекта подставляются в промпт."""

    def test_project_name_and_root_injected(self):
        prompt = _build_system_prompt(AgentContext(project_name="OlliDesk", project_root="D:/proj"))

        assert "Проект: OlliDesk" in prompt
        assert "Корень проекта: D:/proj" in prompt

    def test_project_block_omitted_without_data(self):
        prompt = _build_system_prompt(AgentContext())

        assert "Корень проекта:" not in prompt


class TestSystemPromptBudget:
    """Переменные секции промпта укладываются в бюджет окна модели."""

    def test_oversize_rag_truncated(self):
        prompt = _build_system_prompt(AgentContext(rag_context="слово " * 5000, num_ctx=4096))

        assert len(prompt) <= system_prompt_char_budget(4096)
        assert "[контекст обрезан]" in prompt

    def test_no_trimming_when_ctx_unknown(self):
        big = "x" * 50000
        prompt = _build_system_prompt(AgentContext(rag_context=big, num_ctx=0))

        assert big in prompt

    def test_fixed_instructions_survive_trimming(self):
        prompt = _build_system_prompt(AgentContext(rag_context="x" * 50000, num_ctx=512))

        assert "ИНСТРУМЕНТЫ" in prompt
        assert "ЯЗЫК ОТВЕТА" in prompt

    def test_drop_order_rag_before_map(self):
        project_map = "Карта: cloud/ agents/"
        prompt = _build_system_prompt(
            AgentContext(rag_context="R" * 20000, project_map=project_map, num_ctx=4096)
        )

        assert project_map in prompt
        assert len(prompt) <= system_prompt_char_budget(4096)

    def test_rules_survive_over_map(self):
        rules = "Правила проекта: не коммить без просьбы."
        prompt = _build_system_prompt(
            AgentContext(project_map="M" * 20000, extra_rules=rules, num_ctx=4096)
        )

        assert rules in prompt
        assert len(prompt) <= system_prompt_char_budget(4096)