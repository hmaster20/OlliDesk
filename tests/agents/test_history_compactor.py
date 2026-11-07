"""Тесты сжатия истории диалога (agents/history_compactor.py)."""

from typing import cast

from agents.agent_loop import AgentContext, AgentLoop
from agents.base import LLMBackend
from agents.history_compactor import (
    TOOL_RESULT_STUB,
    _drop_oldest_turns,
    _split_turns,
    _stub_old_tool_results,
    compact_history,
)
from agents.ollama_client import ChatChunk, ChatMessage, ToolCall
from agents.tool_registry import ToolRegistry, ToolResult
from core.config import AgentMode, ToolPolicy


def _system(text: str = "ты ассистент") -> ChatMessage:
    return ChatMessage(role="system", content=text)


def _user(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=text)


def _assistant(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=text)


def _tool(text: str) -> ChatMessage:
    return ChatMessage(role="tool", content=text, name="read_file", tool_call_id="c1")


class TestTurnGrouping:
    def test_new_turn_per_user(self):
        turns = _split_turns([_user("1"), _assistant("a"), _tool("t"), _assistant("b"), _user("2")])
        assert len(turns) == 2
        assert len(turns[0]) == 4
        assert len(turns[1]) == 1

    def test_leading_non_user_forms_first_turn(self):
        turns = _split_turns([_assistant("a"), _user("2")])
        assert len(turns) == 2


class TestStubToolResults:
    def test_old_results_stubbed_recent_kept(self):
        dialogue = [
            _user("1"),
            _tool("A"),
            _user("2"),
            _tool("B"),
            _user("3"),
            _tool("C"),
        ]
        out = _stub_old_tool_results(dialogue, keep_recent=3)
        assert out[1].content == TOOL_RESULT_STUB
        assert out[3].content == "B"
        assert out[5].content == "C"

    def test_already_stubbed_not_duplicated(self):
        dialogue = [_user("1"), _tool(TOOL_RESULT_STUB)]
        out = _stub_old_tool_results(dialogue, keep_recent=0)
        assert out[1].content == TOOL_RESULT_STUB


class TestDropOldestTurns:
    def test_keeps_last_turn(self):
        dialogue = [_user("1"), _assistant("a"), _user("2"), _assistant("b"), _user("3")]
        out = _drop_oldest_turns(dialogue, budget=0)
        assert out == [_user("3")]

    def test_keeps_all_when_budget_allows(self):
        dialogue = [_user("1"), _assistant("a")]
        assert _drop_oldest_turns(dialogue, budget=10000) == dialogue


class TestCompactHistory:
    def test_under_threshold_returns_same_object(self):
        messages = [_system(), _user("привет"), _assistant("здравствуй")]
        assert compact_history(messages, num_ctx=4096) is messages

    def test_zero_ctx_is_noop(self):
        messages = [_system(), _user("x")]
        assert compact_history(messages, num_ctx=0) is messages

    def test_system_preserved_and_oldest_turns_dropped(self):
        messages = [_system("курс дела")]
        for i in range(4):
            messages.append(_user(f"turn{i} " + "x" * 1000))
            messages.append(_assistant(f"answer{i} " + "y" * 1000))

        out = compact_history(messages, num_ctx=1200)

        assert out[0].role == "system"
        assert out[0].content == "курс дела"
        assert out[-1].role == "assistant"
        assert out[-1].content.startswith("answer3")
        joined = " ".join(m.content for m in out)
        assert "turn0" not in joined
        assert len(out) < len(messages)

    def test_stubs_tool_results_before_dropping(self):
        messages = [_system()]
        for i in range(6):
            messages.append(_user(f"q{i}"))
            messages.append(
                ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id="c", name="read_file", arguments={"path": "a"})],
                )
            )
            messages.append(_tool("Z" * 2000))
            messages.append(_assistant("готово"))
        total_before = len(messages)

        out = compact_history(messages, num_ctx=4096)

        assert out[0].role == "system"
        # Часть старых tool-результатов опустошена.
        assert any(m.role == "tool" and m.content == TOOL_RESULT_STUB for m in out)
        # Последний tool-результат цел.
        tool_results = [m.content for m in out if m.role == "tool"]
        assert tool_results[-1] == "Z" * 2000
        assert len(out) <= total_before


class _RecordingClient:
    """Запоминает промпт, уходящий в chat(), и сразу отдаёт финальный ответ."""

    def __init__(self, num_ctx: int):
        self.num_ctx = num_ctx
        self.seen: list[ChatMessage] | None = None

    @property
    def is_cancelled(self) -> bool:
        return False

    async def chat(self, model, messages, tools=None, stream=True):
        self.seen = messages
        yield ChatChunk(content="ok", done=True)


class _FakeRegistry:
    def get_schemas(self, mode):
        return [object()]

    def get_policy(self, name):
        return ToolPolicy.AUTO

    def get_description(self, name):
        return "описание"

    async def execute(self, tool_call, **extra):
        return ToolResult(tool_call.id, tool_call.name, "x", False)


async def _noop_token(_: str):
    pass


async def _approve(name, description, arguments):
    return True


class TestAgentLoopIntegration:
    async def test_long_history_is_compacted_before_chat(self):
        client = _RecordingClient(num_ctx=1200)
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        history: list[ChatMessage] = []
        for i in range(4):
            history.append(_user(f"turn{i} " + "x" * 1000))
            history.append(_assistant(f"answer{i} " + "y" * 1000))

        await loop.run(
            context=AgentContext(mode=AgentMode.AGENT),
            messages_history=history,
            on_token=_noop_token,
            on_tool_request=_approve,
            max_iterations=1,
        )

        assert client.seen is not None
        assert client.seen[0].role == "system"
        assert len(client.seen) < 1 + len(history)

    async def test_unknown_num_ctx_leaves_history_intact(self):
        class _NoCtx(_RecordingClient):
            pass

        client = _NoCtx(num_ctx=0)
        loop = AgentLoop(
            client=cast(LLMBackend, client),
            registry=cast(ToolRegistry, _FakeRegistry()),
            model="m",
        )
        messages = [_system(), _user("x")]
        assert loop._compact_history(messages) is messages
