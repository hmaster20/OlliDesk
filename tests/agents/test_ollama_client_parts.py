"""Тесты ChatMessage/ChatChunk на базе MessagePart (4.8.A).

Ключевой инвариант: старые конструкторы (`ChatMessage(content=...)`,
`ChatChunk(content=...)`) работают, read-only свойства отдают ту же строку.
Плюс — разбор `message` из Ollama на отдельные ChatChunk-события.
"""

from __future__ import annotations

from agents.ollama_client import (
    ChatChunk,
    ChatMessage,
    OllamaClient,
    ReasoningPart,
    TextPart,
    ToolCall,
    ToolCallPart,
    ToolResultPart,
)


class TestChatMessageBackwardCompat:
    def test_content_only(self) -> None:
        m = ChatMessage(role="user", content="привет")
        assert m.content == "привет"
        assert m.reasoning_content == ""
        assert m.tool_calls is None
        assert len(m.parts) == 1
        assert isinstance(m.parts[0], TextPart)
        assert m.parts[0].text == "привет"

    def test_empty_content(self) -> None:
        m = ChatMessage(role="assistant", content="")
        assert m.content == ""
        assert m.parts == []

    def test_reasoning_and_content(self) -> None:
        m = ChatMessage(role="assistant", content="ответ", reasoning_content="думаю")
        assert m.content == "ответ"
        assert m.reasoning_content == "думаю"
        # Порядок: reasoning → text (соответствует стриму).
        assert len(m.parts) == 2
        assert isinstance(m.parts[0], ReasoningPart)
        assert isinstance(m.parts[1], TextPart)

    def test_tool_calls_only(self) -> None:
        tc = ToolCall(name="read_file", arguments={"path": "a.py"})
        m = ChatMessage(role="assistant", content="", tool_calls=[tc])
        assert m.content == ""
        assert m.tool_calls is not None
        assert len(m.tool_calls) == 1
        assert m.tool_calls[0].name == "read_file"

    def test_all_three_fields(self) -> None:
        tc = ToolCall(name="read_file", arguments={"path": "a.py"})
        m = ChatMessage(
            role="assistant",
            content="ответ",
            reasoning_content="думаю",
            tool_calls=[tc],
        )
        assert m.content == "ответ"
        assert m.reasoning_content == "думаю"
        assert m.tool_calls == [tc]
        # Порядок частей: reasoning → text → tool_call.
        kinds = [p.kind for p in m.parts]
        assert kinds == ["reasoning", "text", "tool_call"]

    def test_parts_priority_over_content(self) -> None:
        """Если parts переданы — старые поля молча игнорируются."""
        m = ChatMessage(
            role="assistant",
            parts=[TextPart(text="из parts")],
            content="из content",  # type: ignore[call-arg]
        )
        assert m.content == "из parts"

    def test_tool_message_keeps_name_and_call_id(self) -> None:
        m = ChatMessage(
            role="tool",
            content="результат",
            tool_call_id="call_1",
            name="read_file",
        )
        assert m.role == "tool"
        assert m.content == "результат"
        assert m.tool_call_id == "call_1"
        assert m.name == "read_file"


class TestChatMessageParts:
    def test_explicit_parts(self) -> None:
        m = ChatMessage(
            role="assistant",
            parts=[
                ReasoningPart(text="шаг 1"),
                TextPart(text="итог"),
                ToolCallPart(calls=[ToolCall(name="x", arguments={})]),
            ],
        )
        assert m.reasoning_content == "шаг 1"
        assert m.content == "итог"
        assert m.tool_calls is not None
        assert m.tool_calls[0].name == "x"

    def test_multiple_text_parts_concatenated(self) -> None:
        m = ChatMessage(
            role="assistant",
            parts=[TextPart(text="a"), TextPart(text="b"), TextPart(text="c")],
        )
        assert m.content == "abc"

    def test_multiple_tool_call_parts_concatenated(self) -> None:
        m = ChatMessage(
            role="assistant",
            parts=[
                ToolCallPart(calls=[ToolCall(name="a", arguments={})]),
                ToolCallPart(calls=[ToolCall(name="b", arguments={})]),
            ],
        )
        assert m.tool_calls is not None
        assert len(m.tool_calls) == 2

    def test_with_content_replaces_text(self) -> None:
        m = ChatMessage(role="tool", content="длинный результат", tool_call_id="c1", name="read_file")
        m2 = m.with_content("[обрезано]")
        assert m2.content == "[обрезано]"
        assert m2.tool_call_id == "c1"
        assert m2.name == "read_file"
        # Исходный не изменился — with_content возвращает копию.
        assert m.content == "длинный результат"

    def test_with_content_preserves_reasoning(self) -> None:
        m = ChatMessage(
            role="assistant",
            parts=[ReasoningPart(text="думаю"), TextPart(text="старый")],
        )
        m2 = m.with_content("новый")
        assert m2.reasoning_content == "думаю"
        assert m2.content == "новый"

    def test_with_content_adds_text_if_missing(self) -> None:
        m = ChatMessage(role="assistant", parts=[ReasoningPart(text="думаю")])
        m2 = m.with_content("готово")
        assert m2.content == "готово"
        assert m2.reasoning_content == "думаю"

    def test_to_ollama_dict(self) -> None:
        tc = ToolCall(name="read_file", arguments={"path": "a.py"})
        m = ChatMessage(role="assistant", content="ответ", tool_calls=[tc])
        d = m.to_ollama_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "ответ"
        assert d["tool_calls"][0]["function"]["name"] == "read_file"


class TestChatChunkBackwardCompat:
    def test_text_chunk(self) -> None:
        c = ChatChunk(content="hello")
        assert c.content == "hello"
        assert c.reasoning_content == ""
        assert c.tool_calls is None
        assert isinstance(c.part, TextPart)
        assert c.event_type == "delta"
        assert c.done is False

    def test_reasoning_chunk(self) -> None:
        c = ChatChunk(reasoning_content="думаю")
        assert c.reasoning_content == "думаю"
        assert c.content == ""
        assert isinstance(c.part, ReasoningPart)

    def test_tool_call_chunk(self) -> None:
        tc = ToolCall(name="x", arguments={})
        c = ChatChunk(tool_calls=[tc])
        assert c.tool_calls is not None
        assert len(c.tool_calls) == 1
        assert isinstance(c.part, ToolCallPart)
        assert c.event_type == "tool_call"

    def test_done_end_chunk(self) -> None:
        c = ChatChunk(done=True, event_type="end")
        assert c.done is True
        assert c.part is None
        assert c.content == ""
        assert c.reasoning_content == ""

    def test_empty_chunk(self) -> None:
        c = ChatChunk(content="")
        assert c.content == ""
        assert c.reasoning_content == ""
        assert c.tool_calls is None
        assert c.part is None

    def test_explicit_part_priority(self) -> None:
        c = ChatChunk(
            part=TextPart(text="из parts"),
            content="из content",  # type: ignore[call-arg]
            done=True,
        )
        assert c.content == "из parts"
        assert c.done is True


class TestOllamaMessageParsing:
    """`_chunks_from_ollama_message` — разбор одной строки ответа Ollama."""

    def test_only_content(self) -> None:
        chunks = OllamaClient._chunks_from_ollama_message({"content": "abc"})
        assert len(chunks) == 1
        assert chunks[0].content == "abc"
        assert chunks[0].event_type == "delta"

    def test_only_reasoning(self) -> None:
        chunks = OllamaClient._chunks_from_ollama_message({"reasoning_content": "мысль"})
        assert len(chunks) == 1
        assert chunks[0].reasoning_content == "мысль"
        assert isinstance(chunks[0].part, ReasoningPart)

    def test_reasoning_then_content(self) -> None:
        chunks = OllamaClient._chunks_from_ollama_message({"reasoning_content": "мысль", "content": "ответ"})
        assert len(chunks) == 2
        assert isinstance(chunks[0].part, ReasoningPart)
        assert isinstance(chunks[1].part, TextPart)

    def test_tool_calls_only(self) -> None:
        chunks = OllamaClient._chunks_from_ollama_message(
            {
                "content": "",
                "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.py"}}}],
            }
        )
        assert len(chunks) == 1
        assert chunks[0].tool_calls is not None
        assert chunks[0].tool_calls[0].name == "read_file"
        assert chunks[0].event_type == "tool_call"

    def test_all_three(self) -> None:
        chunks = OllamaClient._chunks_from_ollama_message(
            {
                "reasoning_content": "мысль",
                "content": "ответ",
                "tool_calls": [{"function": {"name": "x", "arguments": {}}}],
            }
        )
        assert len(chunks) == 3
        kinds = [c.event_type for c in chunks]
        assert kinds == ["delta", "delta", "tool_call"]

    def test_empty_message(self) -> None:
        chunks = OllamaClient._chunks_from_ollama_message({})
        assert chunks == []

    def test_malformed_tool_calls_ignored(self) -> None:
        # Битый tool_calls не должен ронять разбор.
        chunks = OllamaClient._chunks_from_ollama_message({"tool_calls": [{"garbage": "no function key"}]})
        # Контента нет, tool_call тоже отброшен — пустой результат.
        assert chunks == []


class TestToolResultPart:
    """ToolResultPart объявлен в схеме для будущей миграции role='tool'."""

    def test_fields_default(self) -> None:
        p = ToolResultPart()
        assert p.kind == "tool_result"
        assert p.call_id == ""
        assert p.name == ""
        assert p.content == ""
        assert p.is_error is False

    def test_explicit(self) -> None:
        p = ToolResultPart(call_id="c1", name="read_file", content="x", is_error=True)
        assert p.call_id == "c1"
        assert p.is_error is True
