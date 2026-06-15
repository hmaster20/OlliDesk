"""Асинхронный клиент Ollama API."""

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from core.exceptions import ModelNotFoundError, OllamaConnectionError, ToolsNotSupportedError


class OllamaModelInfo(BaseModel):
    """Информация о модели Ollama."""
    name: str
    size: int
    digest: str
    modified_at: str


class ToolCall(BaseModel):
    """Вызов инструмента."""
    id: str = Field(default_factory=lambda: f"call_{id(object())}")
    name: str
    arguments: dict[str, Any]


class ChatMessage(BaseModel):
    """Сообщение в чате."""
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    images: list[str] | None = None  # base64
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None  # для tool-role (Ollama использует name, не tool_call_id)

    def to_ollama_dict(self) -> dict:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.images:
            data["images"] = self.images
        if self.tool_calls:
            data["tool_calls"] = [
                {"function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in self.tool_calls
            ]
        if self.role == "tool" and self.name:
            data["name"] = self.name
        return data


class ChatChunk(BaseModel):
    """Один токен из стрима."""
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] | None = None
    done: bool = False


class ToolSchema(BaseModel):
    """JSON-схема инструмента для Ollama."""
    type: Literal["function"] = "function"
    function: dict[str, Any]


class ChatOptions(BaseModel):
    """Опции чата."""
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    num_predict: int = 2048
    stop: list[str] = Field(default_factory=list)


class OllamaClient:
    """Асинхронный клиент Ollama API."""

    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 300.0):
        """
        Инициализация клиента.

        Args:
            base_url: Базовый URL Ollama API
            timeout: Таймаут запросов в секундах
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._cancel_requested = False

    async def __aenter__(self):
        """Контекстный менеджер: вход."""
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Контекстный менеджер: выход."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Возвращает HTTP-клиент (создает при необходимости)."""
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def list_models(self) -> list[OllamaModelInfo]:
        """
        Получает список доступных моделей.

        Returns:
            list[OllamaModelInfo]: Список моделей

        Raises:
            OllamaConnectionError: Если не удалось подключиться
        """
        client = await self._get_client()
        try:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            models = [OllamaModelInfo(**m) for m in data.get("models", [])]
            logger.debug(f"Найдено моделей: {len(models)}")
            return models
        except httpx.ConnectError as e:
            raise OllamaConnectionError(f"Не удалось подключиться к Ollama: {e}") from e
        except httpx.HTTPError as e:
            raise OllamaConnectionError(f"Ошибка HTTP: {e}") from e

    def cancel_request(self):
        """Отменяет текущий стриминг-запрос (безопасно из любого потока)."""
        self._cancel_requested = True

    def _check_cancelled(self):
        """Поднимает StopAsyncIteration если запрос отменён."""
        if self._cancel_requested:
            raise StopAsyncIteration()

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None = None,
        options: ChatOptions | None = None,
        stream: bool = True,
    ) -> AsyncIterator[ChatChunk]:
        """
        Отправляет запрос в чат со стримингом.

        Args:
            model: Имя модели
            messages: Список сообщений
            tools: Список инструментов (JSON-схемы)
            options: Опции чата
            stream: Использовать стриминг

        Yields:
            ChatChunk: Токены из стрима

        Raises:
            OllamaConnectionError: Если не удалось подключиться
            ModelNotFoundError: Если модель не найдена
        """
        client = await self._get_client()

        # Формируем запрос
        request_data = {
            "model": model,
            "messages": [m.to_ollama_dict() for m in messages],
            "stream": stream,
        }

        if tools:
            request_data["tools"] = [t.model_dump() for t in tools]

        if options:
            request_data["options"] = options.model_dump()

        req_preview = json.dumps(request_data, default=str, ensure_ascii=False)[:2000]
        logger.info(f"Ollama request: {req_preview}")
        try:
            if stream:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=request_data,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        body = response.text[:2000]
                        logger.error(f"Ollama {response.status_code}: {body}")
                        if response.status_code == 404:
                            raise ModelNotFoundError(f"Модель '{model}' не найдена")
                        if "does not support tools" in body:
                            raise ToolsNotSupportedError(
                                f"Модель '{model}' не поддерживает вызов инструментов"
                            )
                        response.raise_for_status()
                    async for line in response.aiter_lines():
                        self._check_cancelled()
                        if line:
                            chunk_data = json.loads(line)
                            msg = chunk_data.get("message", {})
                            chunk = ChatChunk(
                                content=msg.get("content", ""),
                                reasoning_content=msg.get("reasoning_content", ""),
                                tool_calls=[
                                    ToolCall(**tc["function"])
                                    for tc in msg.get("tool_calls", [])
                                ] if msg.get("tool_calls") else None,
                                done=chunk_data.get("done", False),
                            )
                            yield chunk
            else:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=request_data,
                )
                response.raise_for_status()
                data = response.json()
                msg = data.get("message", {})
                yield ChatChunk(
                    content=msg.get("content", ""),
                    reasoning_content=msg.get("reasoning_content", ""),
                    tool_calls=[
                        ToolCall(**tc["function"])
                        for tc in msg.get("tool_calls", [])
                    ] if msg.get("tool_calls") else None,
                    done=True,
                )

        except httpx.ConnectError as e:
            logger.error(f"Ollama ConnectError ({type(e).__name__}): {e!r}")
            raise OllamaConnectionError(f"Не удалось подключиться к Ollama: {e}") from e
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTPStatusError ({e.response.status_code}): {e!r}")
            raise OllamaConnectionError(f"Ошибка HTTP: {e}") from e
        except httpx.HTTPError as e:
            logger.error(f"Ollama HTTPError ({type(e).__name__}): {e!r}")
            raise OllamaConnectionError(f"Ошибка HTTP: {e}") from e

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """
        Получает эмбеддинги для списка текстов.

        Args:
            model: Имя модели
            texts: Список текстов

        Returns:
            list[list[float]]: Список векторов

        Raises:
            OllamaConnectionError: Если не удалось подключиться
            ModelNotFoundError: Если модель не найдена
        """
        client = await self._get_client()

        try:
            response = await client.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": model,
                    "input": texts,
                },
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings", [])
            logger.debug(f"Получено эмбеддингов: {len(embeddings)}")
            return embeddings

        except httpx.ConnectError as e:
            raise OllamaConnectionError(f"Не удалось подключиться к Ollama: {e}") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ModelNotFoundError(f"Модель '{model}' не найдена") from e
            raise OllamaConnectionError(f"Ошибка HTTP: {e}") from e
        except httpx.HTTPError as e:
            raise OllamaConnectionError(f"Ошибка HTTP: {e}") from e
