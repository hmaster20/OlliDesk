from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agents.ollama_client import (
    ChatMessage,
    ChatOptions,
    OllamaClient,
    OllamaModelInfo,
    ToolCall,
    ToolSchema,
)
from core.exceptions import ModelNotFoundError, OllamaConnectionError


@pytest.fixture
def client():
    return OllamaClient(base_url="http://test:11434")


class TestOllamaModelInfo:
    def test_valid_model_info(self):
        info = OllamaModelInfo(
            name="qwen2.5-coder:7b",
            size=12345,
            digest="sha256:abc",
            modified_at="2024-01-01T00:00:00Z",
        )
        assert info.name == "qwen2.5-coder:7b"
        assert info.size == 12345


class TestChatMessage:
    def test_user_message(self):
        msg = ChatMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_assistant_with_tool_calls(self):
        tc = ToolCall(id="call_1", name="read_file", arguments={"path": "main.py"})
        msg = ChatMessage(role="assistant", content="", tool_calls=[tc])
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "read_file"


class TestChatOptions:
    def test_defaults(self):
        opts = ChatOptions()
        assert opts.temperature == 0.7
        assert opts.top_p == 0.9
        assert opts.top_k == 40
        assert opts.num_predict == 2048


class TestToolSchema:
    def test_function_type(self):
        schema = ToolSchema(function={"name": "test"})
        assert schema.type == "function"
        assert schema.function["name"] == "test"


class TestOllamaClient:
    @pytest.mark.asyncio
    async def test_list_models_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {
                    "name": "qwen2.5-coder:7b",
                    "size": 12345,
                    "digest": "sha256:abc",
                    "modified_at": "2024-01-01T00:00:00Z",
                }
            ]
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_client):
            models = await client.list_models()

        assert len(models) == 1
        assert models[0].name == "qwen2.5-coder:7b"
        mock_client.get.assert_called_once_with("http://test:11434/api/tags")

    @pytest.mark.asyncio
    async def test_list_models_connection_error(self, client):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.side_effect = httpx.ConnectError("connection refused")

        with (
            patch.object(client, "_get_client", return_value=mock_client),
            pytest.raises(OllamaConnectionError, match="Не удалось подключиться"),
        ):
            await client.list_models()

    @pytest.mark.asyncio
    async def test_list_models_http_error(self, client):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get.side_effect = httpx.HTTPError("server error")

        with (
            patch.object(client, "_get_client", return_value=mock_client),
            pytest.raises(OllamaConnectionError, match="Ошибка HTTP"),
        ):
            await client.list_models()

    @pytest.mark.asyncio
    async def test_chat_stream(self, client):
        mock_response = AsyncMock()
        mock_response.__aenter__.return_value = mock_response
        mock_response.status_code = 200

        stream_lines = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"message":{"content":" world"},"done":false}',
            '{"message":{"content":""},"done":true}',
        ]

        async def aiter_lines():
            for line in stream_lines:
                yield line

        mock_response.aiter_lines = aiter_lines
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.stream.return_value = mock_response

        messages = [ChatMessage(role="user", content="hi")]

        with patch.object(client, "_get_client", return_value=mock_client):
            chunks = []
            async for chunk in client.chat(model="test-model", messages=messages):
                chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0].content == "Hello"
        assert chunks[1].content == " world"
        assert chunks[2].done is True

    @pytest.mark.asyncio
    async def test_chat_non_stream(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Full response"},
            "done": True,
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        messages = [ChatMessage(role="user", content="hi")]

        with patch.object(client, "_get_client", return_value=mock_client):
            chunks = []
            async for chunk in client.chat(model="test-model", messages=messages, stream=False):
                chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].content == "Full response"
        assert chunks[0].done is True

    @pytest.mark.asyncio
    async def test_chat_with_tools(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "name": "read_file", "arguments": {"path": "main.py"}}
                ],
            },
            "done": True,
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        messages = [ChatMessage(role="user", content="read main.py")]
        tool_schema = ToolSchema(function={"name": "read_file", "parameters": {"type": "object"}})

        with patch.object(client, "_get_client", return_value=mock_client):
            chunks = []
            async for chunk in client.chat(
                model="test-model", messages=messages, tools=[tool_schema], stream=False
            ):
                chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].tool_calls is not None
        assert chunks[0].tool_calls[0].name == "read_file"

    @pytest.mark.asyncio
    async def test_chat_connection_error(self, client):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.stream.side_effect = httpx.ConnectError("connection refused")

        messages = [ChatMessage(role="user", content="hi")]

        with (
            patch.object(client, "_get_client", return_value=mock_client),
            pytest.raises(OllamaConnectionError, match="Не удалось подключиться"),
        ):
            async for _ in client.chat(model="test-model", messages=messages):
                pass

    @pytest.mark.asyncio
    async def test_chat_model_not_found(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.stream.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=mock_response
        )

        messages = [ChatMessage(role="user", content="hi")]

        with (
            patch.object(client, "_get_client", return_value=mock_client),
            pytest.raises(ModelNotFoundError, match="не найдена"),
        ):
            async for _ in client.chat(model="nonexistent", messages=messages):
                pass

    @pytest.mark.asyncio
    async def test_embed_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_client):
            embeddings = await client.embed(
                model="nomic-embed-text",
                texts=["hello", "world"],
            )

        assert len(embeddings) == 2
        assert embeddings[0] == [0.1, 0.2, 0.3]
        assert embeddings[1] == [0.4, 0.5, 0.6]

    @pytest.mark.asyncio
    async def test_embed_connection_error(self, client):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        with (
            patch.object(client, "_get_client", return_value=mock_client),
            pytest.raises(OllamaConnectionError, match="Не удалось подключиться"),
        ):
            await client.embed(model="test", texts=["hi"])

    @pytest.mark.asyncio
    async def test_chat_with_options(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": {"content": "ok"}, "done": True}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_response

        messages = [ChatMessage(role="user", content="hi")]
        options = ChatOptions(temperature=0.5)

        with patch.object(client, "_get_client", return_value=mock_client):
            async for _ in client.chat(
                model="test", messages=messages, options=options, stream=False
            ):
                pass

        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["json"]["options"]["temperature"] == 0.5

    def test_context_manager(self):
        client = OllamaClient()
        assert client._client is None

    async def test_client_lifecycle(self):
        client = OllamaClient()
        c = await client._get_client()
        assert c is not None
        assert isinstance(c, httpx.AsyncClient)
        await c.aclose()
