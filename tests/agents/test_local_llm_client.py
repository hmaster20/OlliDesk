"""Тесты авто-бюджета контекста в LocalLLMClient.

Экземпляр клиента создаётся через ``object.__new__``: полноценный ``__init__``
требует llama_cpp и загрузки GGUF-файла. Логике ``_fit_messages`` нужны только
``n_ctx`` и токенизатор, поэтому подставляем заглушку.
"""

from typing import Any, cast

from agents.local_llm_client import _LANGUAGE_INSTRUCTION, LocalLLMClient
from agents.ollama_client import ChatMessage, ToolSchema


class _DummyTokenizer:
    """1 токен на каждый байт UTF-8 — предсказуемо для тестов."""

    def tokenize(self, data: bytes, add_bos: bool = False) -> list[int]:
        return list(data)

    def detokenize(self, tokens: list[int]) -> bytes:
        return bytes(tokens)


def _make_client(n_ctx: int) -> LocalLLMClient:
    client = object.__new__(LocalLLMClient)
    client.n_ctx = n_ctx
    client.max_tokens = 64
    client.llm = cast(Any, _DummyTokenizer())
    return client


def _tokens(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "").encode("utf-8")) for m in messages)


class TestFitMessages:
    """Обрезка промпта под окно контекста."""

    def test_small_prompt_untouched(self):
        client = _make_client(n_ctx=4096)
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "привет"},
        ]
        assert client._fit_messages(messages, reserve_tokens=64) == messages

    def test_keeps_system_and_latest_when_over_budget(self):
        client = _make_client(n_ctx=600)
        messages = [
            {"role": "system", "content": "s" * 40},
            {"role": "user", "content": "A" * 300},
            {"role": "assistant", "content": "B" * 300},
            {"role": "user", "content": "new" * 5},
        ]
        fitted = client._fit_messages(messages, reserve_tokens=100)
        assert fitted[0]["role"] == "system"
        assert fitted[-1]["content"] == "new" * 5
        # Самый старый «серединный» диалог выброшен.
        assert {"role": "user", "content": "A" * 300} not in fitted

    def test_oversized_system_truncated(self):
        client = _make_client(n_ctx=500)
        messages = [
            {"role": "system", "content": "S" * 1000},
            {"role": "user", "content": "hi"},
        ]
        fitted = client._fit_messages(messages, reserve_tokens=100)
        # В бюджет (n_ctx - reserve = 400) системный промпт целиком не влезает —
        # он должен быть подрезан, но остаться системным.
        assert fitted[0]["role"] == "system"
        assert len(fitted[0]["content"]) < 1000
        # Итог должен укладываться в окно (небольшой запас на маркер обрезки).
        assert _tokens(fitted) <= client.n_ctx

    def test_last_message_truncated_if_alone_too_big(self):
        client = _make_client(n_ctx=500)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "X" * 5000},
        ]
        fitted = client._fit_messages(messages, reserve_tokens=100)
        assert len(str(fitted[-1]["content"])) < 5000


class TestPrepareMessages:
    """Языковая директива и инструкция по инструментам в системном сообщении."""

    def test_language_instruction_created_when_no_system(self):
        client = _make_client(n_ctx=4096)
        prepared = client._prepare_messages([ChatMessage(role="user", content="привет")], None)
        assert prepared[0]["role"] == "system"
        assert str(prepared[0]["content"]).startswith(_LANGUAGE_INSTRUCTION)

    def test_language_instruction_prepended_to_existing_system(self):
        client = _make_client(n_ctx=4096)
        prepared = client._prepare_messages([ChatMessage(role="system", content="Ты ассистент")], None)
        assert prepared[0]["content"] == f"{_LANGUAGE_INSTRUCTION}\n\nТы ассистент"

    def test_language_instruction_precedes_tool_instruction(self):
        client = _make_client(n_ctx=4096)
        tools = [
            ToolSchema(
                function={
                    "name": "read_file",
                    "description": "Читает файл",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                }
            )
        ]
        prepared = client._prepare_messages([ChatMessage(role="system", content="Ты ассистент")], tools)
        content = str(prepared[0]["content"])
        assert content.startswith(_LANGUAGE_INSTRUCTION)
        assert "read_file" in content
        # Языковая директива идёт раньше инструкции по инструментам.
        assert content.index(_LANGUAGE_INSTRUCTION) < content.index("read_file")
