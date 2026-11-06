"""Асинхронный клиент Ollama API."""

import json
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Annotated, Any, Literal, cast

import httpx
from loguru import logger
from pydantic import BaseModel, Field, model_validator

from core.context_budget import DEFAULT_NUM_CTX
from core.exceptions import (
    ModelNotFoundError,
    OllamaConnectionError,
    ToolsNotSupportedError,
)


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


# ---------------------------------------------------------------------------
# MessagePart: типизированные части ChatMessage / ChatChunk (4.8.A)
#
# Раньше reasoning_content и tool_calls жили отдельными полями и либо
# терялись при сохранении сессии, либо «склеивались» в один content.
# Теперь — список типизированных частей; ChatMessage.content остаётся
# read-only свойством, собираемым из TextPart (переходный период).
# ---------------------------------------------------------------------------


class TextPart(BaseModel):
    """Текстовая часть сообщения (ответ модели / реплика пользователя)."""

    kind: Literal["text"] = "text"
    text: str = ""


class ReasoningPart(BaseModel):
    """Рассуждения модели (reasoning_content, thinking, chain-of-thought)."""

    kind: Literal["reasoning"] = "reasoning"
    text: str = ""


class ToolCallPart(BaseModel):
    """Запрос модели на вызов одного или нескольких инструментов."""

    kind: Literal["tool_call"] = "tool_call"
    calls: list[ToolCall] = Field(default_factory=list)


class ToolResultPart(BaseModel):
    """Результат выполнения инструмента (для будущей миграции role="tool").

    Сейчас роль ``tool`` кодируется полями ``tool_call_id``/``name`` у
    ChatMessage, но ToolResultPart доступен для перехода на единый формат.
    """

    kind: Literal["tool_result"] = "tool_result"
    call_id: str = ""
    name: str = ""
    content: str = ""
    is_error: bool = False


# Discriminated union: pydantic выбирает конкретный тип по полю ``kind``.
MessagePart = Annotated[
    TextPart | ReasoningPart | ToolCallPart | ToolResultPart,
    Field(discriminator="kind"),
]


class ChatMessage(BaseModel):
    """Сообщение в чате.

    Основное хранилище — ``parts``. ``content`` / ``reasoning_content`` /
    ``tool_calls`` — read-only свойства для обратной совместимости со всем
    кодом, который ещё читает их как строки. Конструкторы вида
    ``ChatMessage(role="user", content="hi")`` работают: ``model_validator``
    конвертирует ``content`` в ``parts=[TextPart(text="hi")]``.
    """

    id: str | None = None  # стабильный id (устраняет дубли user-реплик)
    role: Literal["system", "user", "assistant", "tool"]
    parts: list[MessagePart] = Field(default_factory=list)
    images: list[str] | None = None
    tool_call_id: str | None = None  # для role="tool": id исходного tool_call
    name: str | None = None  # для role="tool": имя инструмента

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_fields(cls, data: Any) -> Any:
        """Принимает старый стиль ``content=/reasoning_content=/tool_calls=``.

        Если ``parts`` уже передан — старые поля игнорируются (см. комментарий
        в `history_compactor._stub_old_tool_results`: там нельзя использовать
        `model_copy(update={"content": ...})`).
        """
        if not isinstance(data, dict):
            return data
        if "parts" in data:
            data.pop("content", None)
            data.pop("reasoning_content", None)
            data.pop("tool_calls", None)
            return data

        parts: list[dict[str, Any]] = []
        reasoning = data.pop("reasoning_content", None)
        if reasoning:
            parts.append({"kind": "reasoning", "text": reasoning})
        content = data.pop("content", None)
        if content:
            parts.append({"kind": "text", "text": content})
        tool_calls = data.pop("tool_calls", None)
        if tool_calls:
            parts.append({"kind": "tool_call", "calls": tool_calls})
        data["parts"] = parts
        return data

    # --- read-only свойства для обратной совместимости ---

    @property
    def content(self) -> str:
        """Собранный текст из всех TextPart."""
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    @property
    def reasoning_content(self) -> str:
        """Собранные рассуждения из всех ReasoningPart."""
        return "".join(p.text for p in self.parts if isinstance(p, ReasoningPart))

    @property
    def tool_calls(self) -> list[ToolCall] | None:
        """Собранные tool_calls из всех ToolCallPart."""
        calls: list[ToolCall] = []
        for p in self.parts:
            if isinstance(p, ToolCallPart):
                calls.extend(p.calls)
        return calls or None

    # --- helper: заменить текст, сохранив остальные части ---

    def with_content(self, new_content: str) -> "ChatMessage":
        """Возвращает копию с заменённым текстом, сохраняя прочие части.

        Используется компактором истории: содержимое старых tool-сообщений
        схлопывается до заглушки, но ``tool_call_id``/``name`` и порядок
        частей сохраняются, чтобы не нарушить структуру вызовов.
        """
        new_parts: list[MessagePart] = []
        replaced = False
        for p in self.parts:
            if not replaced and isinstance(p, TextPart):
                new_parts.append(TextPart(text=new_content))
                replaced = True
            else:
                new_parts.append(p)
        if not replaced:
            new_parts.insert(0, TextPart(text=new_content))
        return ChatMessage(
            id=self.id,
            role=self.role,
            parts=new_parts,
            images=self.images,
            tool_call_id=self.tool_call_id,
            name=self.name,
        )

    def to_ollama_dict(self) -> dict:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.images:
            data["images"] = self.images
        if self.tool_calls:
            data["tool_calls"] = [
                {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in self.tool_calls
            ]
        if self.role == "tool" and self.name:
            data["name"] = self.name
        return data


class ChatChunk(BaseModel):
    """Один элемент стрима.

    Основное хранилище — ``part`` + ``event_type``. ``content`` /
    ``reasoning_content`` / ``tool_calls`` — read-only свойства для обратной
    совместимости (см. `ChatMessage`).

    **2.10**: поле ``finish_reason`` заполняется на завершающем
    ``event_type="end"``-чанке. Значения: ``"stop"`` (естественное
    завершение), ``"length"`` (модель обрезалась по ``max_tokens``),
    ``"tool_calls"`` (модель вызвала инструмент; на практике совпадает
    с ``"stop"`` + ``ToolCallPart``), а также значения Ollama
    (``"load"``, ``"unload"``). ``None`` — клиент не сообщил причину
    (эвристика в `AgentLoop`: ``stop``, если нет ``tool_calls``).
    """

    part: MessagePart | None = None
    event_type: Literal["start", "delta", "end", "tool_call"] | None = None
    done: bool = False
    finish_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_fields(cls, data: Any) -> Any:
        """Принимает старый стиль ``content=/reasoning_content=/tool_calls=``."""
        if not isinstance(data, dict):
            return data
        if "part" in data:
            data.pop("content", None)
            data.pop("reasoning_content", None)
            data.pop("tool_calls", None)
            return data
        ...


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

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 300.0,
        num_ctx: int = DEFAULT_NUM_CTX,
    ):
        """
        Инициализация клиента.

        Args:
            base_url: Базовый URL Ollama API
            timeout: Таймаут запросов в секундах
            num_ctx: Размер окна контекста модели в токенах
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.num_ctx = num_ctx
        self._client: httpx.AsyncClient | None = None
        self._cancel_requested = False

    async def __aenter__(self) -> "OllamaClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def list_models(self) -> list[OllamaModelInfo]:
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

    async def is_available(self) -> bool:
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/api/tags", timeout=3.0)
            return response.status_code == 200
        except Exception:
            return False

    def cancel_request(self) -> None:
        self._cancel_requested = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_requested

    def reset_cancel(self) -> None:
        self._cancel_requested = False

    @staticmethod
    def _chunks_from_ollama_message(msg: dict[str, Any]) -> list[ChatChunk]:
        """Разбирает `message` из ответа Ollama на отдельные ChatChunk.

        Возвращает: reasoning-delta, text-delta, tool_call — в порядке,
        в котором их принято показывать (сначала мысли, потом текст, потом
        вызовы). Если все поля пустые — пустой список.
        """
        chunks: list[ChatChunk] = []
        # reasoning_content — старый контракт; thinking — стандарт Ollama
        # для thinking-моделей (DeepSeek-R1, QwQ).
        reasoning = msg.get("reasoning_content") or msg.get("thinking") or ""
        if reasoning:
            chunks.append(ChatChunk(part=ReasoningPart(text=reasoning), event_type="delta"))
        content = msg.get("content", "")
        if content:
            chunks.append(ChatChunk(part=TextPart(text=content), event_type="delta"))
        raw_calls = msg.get("tool_calls")
        if raw_calls:
            try:
                calls = [ToolCall(**tc["function"]) for tc in raw_calls]
            except (KeyError, TypeError) as e:
                logger.warning(f"Некорректный tool_calls от Ollama: {e}; {raw_calls!r}")
                calls = []
            if calls:
                chunks.append(ChatChunk(part=ToolCallPart(calls=calls), event_type="tool_call"))
        return chunks

    # ------------------------------------------------------------------
    # chat(): HTTP-стриминг к POST /api/chat
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # chat(): HTTP-стриминг к POST /api/chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[Any] | None = None,
        options: Any | None = None,
        stream: bool = True,
        op_type: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        """Стриминг ответа Ollama через POST /api/chat.

        Контракт событий (4.8.E), симметричный LocalLLMClient:

          * ``event_type="start"`` — до открытия HTTP-стрима;
          * ``event_type="delta"`` — TextPart/ReasoningPart по мере генерации;
          * ``event_type="tool_call"`` — ToolCallPart (обычно до ``done``);
          * ``event_type="end"``, ``done=True`` — последний чанк.

        Отмена: ``cancel_request()`` выставляет флаг, проверяется между
        чанками. При отмене эмитим ``end`` и выходим — httpx-стрим
        корректно закроется через ``async with``.

        Исключения:
          * 404 → ``ModelNotFoundError``;
          * 400 «does not support tools» → ``ToolsNotSupportedError``;
          * прочие HTTP/сетевые → ``OllamaConnectionError``.
        """
        self.reset_cancel()
        client = await self._get_client()

        # --- payload ---
        options_dict: dict[str, Any] = {"num_ctx": self.num_ctx}
        if options is not None:
            for attr in ("temperature", "top_p", "top_k", "num_predict", "stop"):
                value = getattr(options, attr, None)
                if value is not None:
                    options_dict[attr] = value

        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_ollama_dict() for m in messages],
            "stream": bool(stream),
            "options": options_dict,
        }
        if tools:
            payload["tools"] = [t.model_dump() if hasattr(t, "model_dump") else t for t in tools]

        # [4.8.E] start-event до открытия HTTP-стрима.
        yield ChatChunk(event_type="start", done=False)

        try:
            if stream:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        await self._raise_stream_error(response, model)

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if self._cancel_requested:
                            logger.info("Ollama stream cancelled by user")
                            # 2.10: отмена — не «length», а явный None.
                            # AgentLoop проверит is_cancelled и вернёт "Отменено".
                            yield ChatChunk(event_type="end", done=True)
                            return
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning(f"Некорректный JSON от Ollama: {line[:200]!r}")
                            continue
                        if data.get("error"):
                            raise OllamaConnectionError(f"Ollama: {data['error']}")

                        message = data.get("message") or {}
                        for chunk in self._chunks_from_ollama_message(message):
                            yield chunk

                        if data.get("done"):
                            # 2.10: finish_reason из Ollama (done_reason).
                            # Значения: "stop" | "length" | "load" | "unload".
                            # None — старая Ollama, без поля.
                            yield ChatChunk(
                                event_type="end",
                                done=True,
                                finish_reason=data.get("done_reason"),
                            )
                            return

                # Поток закрылся без done — крайний случай (OOM/обрыв сети).
                # 2.10: считаем "length", чтобы AgentLoop не ушёл в бесконечный
                # retry с тем же промптом.
                yield ChatChunk(event_type="end", done=True, finish_reason="length")
            else:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                )
                if response.status_code >= 400:
                    self._raise_sync_error(response, model)
                data = response.json()
                if data.get("error"):
                    raise OllamaConnectionError(f"Ollama: {data['error']}")
                message = data.get("message") or {}
                for chunk in self._chunks_from_ollama_message(message):
                    yield chunk
                yield ChatChunk(
                    event_type="end",
                    done=True,
                    finish_reason=data.get("done_reason"),
                )

        # ВАЖНО: порядок except — от самого специфичного к общему.
        # HTTPStatusError (404/400/5xx) приходит напрямую от httpx, когда
        # транспорт уже знает код (например, `stream.side_effect` в тестах
        # или `post` без stream-обёртки). Классифицируем так же, как
        # ошибку из response в `_raise_stream_error`/`_raise_sync_error`.
        except httpx.HTTPStatusError as e:
            response = e.response
            status = response.status_code if response is not None else 0
            try:
                body_text = response.text if response is not None else ""
            except Exception:
                body_text = ""
            self._classify_http_error(status, body_text, model)
        except httpx.ConnectError as e:
            raise OllamaConnectionError(f"Не удалось подключиться к Ollama: {e}") from e
        except httpx.HTTPError as e:
            raise OllamaConnectionError(f"Ошибка HTTP: {e}") from e

    # ------------------------------------------------------------------
    # Хелперы классификации HTTP-ошибок для chat()
    # ------------------------------------------------------------------

    async def _raise_stream_error(self, response: httpx.Response, model: str) -> None:
        """Считывает тело стрим-ответа при ошибке и бросает нужное исключение.

        В streaming-режиме ``response.raise_for_status()`` не читает тело,
        а нам оно нужно, чтобы отличить «does not support tools» от
        обычной 400.
        """
        try:
            body = await response.aread()
            body_text = body.decode("utf-8", errors="replace")
        except Exception:
            body_text = ""
        self._classify_http_error(response.status_code, body_text, model)

    def _raise_sync_error(self, response: httpx.Response, model: str) -> None:
        """То же для не-стрим запроса (тело уже прочитано httpx)."""
        try:
            body_text = response.text
        except Exception:
            body_text = ""
        self._classify_http_error(response.status_code, body_text, model)

    @staticmethod
    def _classify_http_error(status_code: int, body_text: str, model: str) -> None:
        """Единая классификация ошибок Ollama — поднимает нужное исключение."""
        lowered = body_text.lower()
        if status_code == 400 and "does not support tools" in lowered:
            raise ToolsNotSupportedError(f"Модель '{model}' не поддерживает инструменты")
        if status_code == 404:
            raise ModelNotFoundError(f"Модель '{model}' не найдена")
        raise OllamaConnectionError(f"HTTP {status_code} от Ollama: {body_text[:500] or '(пустое тело)'}")

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        client = await self._get_client()
        try:
            response = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": texts},
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings", [])
            logger.debug(f"Получено эмбеддингов: {len(embeddings)}")
            return cast(list[list[float]], embeddings)
        except httpx.ConnectError as e:
            raise OllamaConnectionError(f"Не удалось подключиться к Ollama: {e}") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ModelNotFoundError(f"Модель '{model}' не найдена") from e
            raise OllamaConnectionError(f"Ошибка HTTP: {e}") from e
        except httpx.HTTPError as e:
            raise OllamaConnectionError(f"Ошибка HTTP: {e}") from e

    async def check_tools_support(self, model: str) -> bool:
        dummy_tool = ToolSchema(
            function={
                "name": "check_tools_support",
                "description": "Dummy tool to check support",
                "parameters": {"type": "object", "properties": {}},
            }
        )
        client = await self._get_client()
        request_data = {
            "model": model,
            "messages": [{"role": "user", "content": "test"}],
            "tools": [dummy_tool.model_dump()],
            "stream": False,
        }
        try:
            response = await client.post(f"{self.base_url}/api/chat", json=request_data)
            response.raise_for_status()
            return True
        except httpx.ConnectError as e:
            # [FIX] Сервис недоступен — это НЕ «модель не поддерживает tools»,
            # а «нет соединения». Пробрасываем отдельным исключением, чтобы
            # ModelCheckThread мог прервать перебор 39 моделей после первой
            # же сетевой ошибки, а не ждать 39 × 5 сек.
            raise OllamaConnectionError(f"Не удалось подключиться к Ollama: {e}") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400 and "does not support tools" in e.response.text:
                logger.info(f"Модель {model} не поддерживает инструменты.")
                return False
            logger.warning(f"HTTP ошибка при проверке инструментов для {model}: {e}")
            return False
        except Exception as e:
            logger.error(f"Неожиданная ошибка при проверке инструментов для {model}: {e}")
            return False
