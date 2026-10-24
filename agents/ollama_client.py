"""Асинхронный клиент Ollama API."""

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Annotated, Any, Literal, cast

import httpx
from loguru import logger
from pydantic import BaseModel, Field, model_validator

from core.context_budget import DEFAULT_NUM_CTX
from core.exceptions import ModelNotFoundError, OllamaConnectionError


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
    """

    part: MessagePart | None = None
    event_type: Literal["start", "delta", "end", "tool_call"] | None = None
    done: bool = False

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

        reasoning = data.pop("reasoning_content", None)
        content = data.pop("content", None)
        tool_calls = data.pop("tool_calls", None)
        if reasoning:
            data["part"] = {"kind": "reasoning", "text": reasoning}
            data.setdefault("event_type", "delta")
        elif content:
            data["part"] = {"kind": "text", "text": content}
            data.setdefault("event_type", "delta")
        elif tool_calls:
            data["part"] = {"kind": "tool_call", "calls": tool_calls}
            data.setdefault("event_type", "tool_call")
        return data

    @property
    def content(self) -> str:
        if isinstance(self.part, TextPart):
            return self.part.text
        return ""

    @property
    def reasoning_content(self) -> str:
        if isinstance(self.part, ReasoningPart):
            return self.part.text
        return ""

    @property
    def tool_calls(self) -> list[ToolCall] | None:
        if isinstance(self.part, ToolCallPart):
            return self.part.calls or None
        return None


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
        reasoning = msg.get("reasoning_content", "")
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
        """Генерирует ответ через llama.cpp с поддержкой JSON-tool-calling.

        [4.8.E] Эмитит явный ``event_type="start"`` до первого токена —
        симметрично OllamaClient.
        """
        self.reset_cancel()

        chat_messages = self._prepare_messages(messages, tools)

        temperature = (
            _TEMPERATURE_BY_OP.get(op_type, _DEFAULT_TEMPERATURE) if op_type else _DEFAULT_TEMPERATURE
        )
        max_tokens = self.max_tokens
        if options:
            temperature = getattr(options, "temperature", temperature)
            max_tokens = getattr(options, "num_predict", self.max_tokens)

        chat_messages = self._fit_messages(chat_messages, reserve_tokens=max_tokens + 64)

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[ChatChunk | None] = asyncio.Queue()

        def _safe_put(chunk: ChatChunk) -> None:
            with contextlib.suppress(RuntimeError):
                asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)

        def _generate() -> None:
            """Синхронная генерация в отдельном потоке."""
            # [4.8.E] start-event до первого токена.
            _safe_put(ChatChunk(event_type="start", done=False))
            cancelled = False
            full_text_parts: list[str] = []
            prompt_chars = sum(len(str(m.get("content") or "")) for m in chat_messages)
            gen_started = time.perf_counter()
            first_token_at: float | None = None
            try:
                with self._lock:
                    response = self.llm.create_chat_completion(
                        messages=cast(Any, chat_messages),
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=0.8,
                        stream=True,
                        stop=["<|im_end|>", "<|im_start|>"],
                    )
                    for chunk in response:
                        if self._cancel_requested:
                            cancelled = True
                            break
                        choices = cast(Any, chunk).get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        content = str(delta.get("content") or "")
                        if content:
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            full_text_parts.append(content)

                gen_ms = (time.perf_counter() - gen_started) * 1000

                if cancelled:
                    self.last_stats = self._make_stats(prompt_chars, 0, gen_ms, None, cancelled=True)
                    logger.info(f"SLM: генерация отменена за {gen_ms:.0f} мс")
                    _safe_put(ChatChunk(done=True, event_type="end"))
                    return

                full_text = "".join(full_text_parts)
                tokens = self._count_tokens(full_text)
                ttft_ms = (first_token_at - gen_started) * 1000 if first_token_at is not None else None
                self.last_stats = self._make_stats(prompt_chars, tokens, gen_ms, ttft_ms)
                logger.info(
                    f"SLM: {tokens} ток. за {gen_ms / 1000:.1f} с "
                    f"({tokens / (gen_ms / 1000):.1f} ток/с)"
                    + (f", TTFT {ttft_ms:.0f} мс" if ttft_ms is not None else "")
                    + f", промпт ~{prompt_chars} симв."
                )

                tool_calls: list[ToolCall] = []
                if tools and full_text.strip():
                    tool_calls = self._parse_tool_calls(full_text, tools)

                if tool_calls:
                    for tc in tool_calls:
                        logger.info(f"SLM: распознан tool_call → {tc.name}({tc.arguments})")
                    _safe_put(
                        ChatChunk(
                            part=ToolCallPart(calls=tool_calls),
                            event_type="tool_call",
                            done=False,
                        )
                    )
                    _safe_put(ChatChunk(done=True, event_type="end"))
                else:
                    if full_text:
                        _safe_put(
                            ChatChunk(
                                part=TextPart(text=full_text),
                                event_type="delta",
                                done=False,
                            )
                        )
                    _safe_put(ChatChunk(done=True, event_type="end"))

            except Exception as e:
                logger.error(f"SLM generation error: {e}")
                _safe_put(
                    ChatChunk(
                        part=TextPart(text=f"\n[Ошибка SLM: {e}]"),
                        event_type="delta",
                        done=True,
                    )
                )

        thread = threading.Thread(target=_generate, daemon=True)
        thread.start()

        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
            if chunk.done:
                break

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
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400 and "does not support tools" in e.response.text:
                logger.info(f"Модель {model} не поддерживает инструменты.")
                return False
            logger.warning(f"HTTP ошибка при проверке инструментов для {model}: {e}")
            return False
        except Exception as e:
            logger.error(f"Неожиданная ошибка при проверке инструментов для {model}: {e}")
            return False
