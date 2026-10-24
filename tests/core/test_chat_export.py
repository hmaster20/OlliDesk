"""Тесты экспорта чата (9.6.3)."""

from __future__ import annotations

from agents.ollama_client import ChatMessage
from core.chat_export import export_to_html, export_to_markdown


class TestExportMarkdown:
    def test_empty(self) -> None:
        md = export_to_markdown([], title="Test")
        assert "# Test" in md
        assert "---" in md

    def test_user_and_assistant(self) -> None:
        messages = [
            ChatMessage(role="user", content="Привет"),
            ChatMessage(role="assistant", content="Здравствуйте!"),
        ]
        md = export_to_markdown(messages)
        assert "👤 Пользователь" in md
        assert "Привет" in md
        assert "🤖 Ассистент" in md
        assert "Здравствуйте!" in md

    def test_tool_in_details_block(self) -> None:
        messages = [
            ChatMessage(role="user", content="Прочитай файл"),
            ChatMessage(
                role="tool",
                content="print('hi')",
                tool_call_id="c1",
                name="read_file",
            ),
        ]
        md = export_to_markdown(messages)
        assert "<details>" in md
        assert "read_file" in md
        assert "print('hi')" in md

    def test_system_as_quote(self) -> None:
        messages = [
            ChatMessage(role="system", content="Ты помощник"),
        ]
        md = export_to_markdown(messages)
        assert "> _Система:_" in md
        assert "Ты помощник" in md

    def test_meta_line(self) -> None:
        md = export_to_markdown(
            [ChatMessage(role="user", content="hi")],
            model="qwen2.5",
            mode="agent",
            profile="Разработка",
            project="MyProj",
        )
        assert "Модель: `qwen2.5`" in md
        assert "Режим: agent" in md
        assert "Профиль: Разработка" in md
        assert "Проект: `MyProj`" in md

    def test_tool_calls_summary(self) -> None:
        from agents.ollama_client import ToolCall

        msg = ChatMessage(role="assistant", content="ok")
        msg.parts.append(
            __import__("agents.ollama_client", fromlist=["ToolCallPart"]).ToolCallPart(
                calls=[ToolCall(name="read_file", arguments={"path": "a.py"})]
            )
        )
        md = export_to_markdown([msg])
        assert "- `read_file`" in md


class TestExportHtml:
    def test_basic(self) -> None:
        messages = [
            ChatMessage(role="user", content="Hi <script>"),
            ChatMessage(role="assistant", content="Hello & bye"),
        ]
        html = export_to_html(messages)
        assert "<!DOCTYPE html>" in html
        assert "<style>" in html
        assert "Hi &lt;script&gt;" in html
        assert "Hello &amp; bye" in html
        assert 'class="msg user"' in html
        assert 'class="msg assistant"' in html

    def test_tool_label(self) -> None:
        messages = [
            ChatMessage(
                role="tool",
                content="result",
                tool_call_id="c1",
                name="read_file",
            )
        ]
        html = export_to_html(messages)
        assert "🔧 read_file" in html
        assert "result" in html

    def test_code_block(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                content="Пример:\n```python\nprint('hi')\n```\nГотово.",
            )
        ]
        html = export_to_html(messages)
        assert "<pre><code>" in html
        assert "print(&#x27;hi&#x27;)" in html or "print('hi')" in html

    def test_empty(self) -> None:
        html = export_to_html([])
        assert "<!DOCTYPE html>" in html
        assert "<body>" in html
