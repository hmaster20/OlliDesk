"""Клиент встроенной SLM через llama.cpp (fallback для Ollama)."""

import asyncio
import contextlib
import json
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, cast

from loguru import logger

from agents.gpu_utils import detect_optimal_threads
from agents.ollama_client import ChatChunk, ChatMessage, ToolCall

# Языковая директива идёт в самое НАЧАЛО системного сообщения: при обрезке
# контекста `_fit_messages` сохраняет голову промпта, поэтому инструкция не
# потеряется. Qwen2.5 (7B, q4) склонен «соскакивать» на китайский — низкая
# температура плюс явный запрет других языков это подавляют.
_LANGUAGE_INSTRUCTION = (
    "ВАЖНО: отвечай на языке пользователя. Если запрос на русском — пиши "
    "ТОЛЬКО на русском, без слов, фраз и иероглифов на других языках."
)

# Температура по умолчанию для встроенной SLM: ниже, чем у Ollama-моделей,
# чтобы снизить дрейф языка и повысить дисциплину вызовов инструментов.
_DEFAULT_TEMPERATURE = 0.3


class LocalLLMClient:
    """Клиент локальной SLM через llama-cpp-python.

    Используется как fallback, когда Ollama недоступен.
    Поддерживает стриминг, простой tool-calling через JSON-парсинг
    и эмбеддинги (если загружена embedding-модель).
    Поддерживает стриминг и «мягкий» tool-calling через JSON-парсинг:
    модель получает инструкцию в system-промпте, а её ответ проверяется
    на наличие JSON вида {"name": ..., "arguments": {...}}.
    """

    def __init__(
        self,
        model_path: str | Path,
        embed_model_path: str | Path | None = None,
        n_ctx: int = 4096,
        n_threads: int = 0,
        n_batch: int = 512,
        n_gpu_layers: int = 0,
        max_tokens: int = 1024,
        verbose: bool = False,
    ):
        """Инициализация клиента.

        Args:
            model_path: Путь к GGUF-файлу chat-модели
            embed_model_path: Путь к GGUF-файлу embedding-модели (опционально)
            n_ctx: Размер контекста в токенах
            n_threads: Количество потоков CPU; 0 или меньше — определить
                автоматически по числу физических ядер
            n_batch: Размер батча для обработки
            n_gpu_layers: Количество слоёв на GPU (0 = CPU-only)
            max_tokens: Лимит генерируемых токенов на ответ по умолчанию
            verbose: Логирование llama.cpp
        """
        from llama_cpp import Llama

        self.model_path = Path(model_path)
        self.embed_model_path = Path(embed_model_path) if embed_model_path else None
        self.n_ctx = n_ctx
        self.n_threads = n_threads if n_threads > 0 else detect_optimal_threads()
        self.n_gpu_layers = n_gpu_layers
        self.max_tokens = max_tokens

        self._cancel_requested = False
        self._lock = threading.Lock()
        self.load_ms: float = 0.0
        self.last_stats: dict[str, Any] = {}

        if not self.model_path.exists():
            raise FileNotFoundError(f"Модель не найдена: {self.model_path}")

        logger.info(
            f"Загрузка локальной SLM: {self.model_path.name} "
            f"(n_ctx={self.n_ctx}, n_threads={self.n_threads}, n_gpu_layers={n_gpu_layers})"
        )
        load_started = time.perf_counter()
        self.llm = Llama(
            model_path=str(self.model_path),
            n_ctx=n_ctx,
            n_threads=self.n_threads,
            n_batch=n_batch,
            n_gpu_layers=n_gpu_layers,
            verbose=verbose,
            chat_format="chatml",  # Qwen2.5 использует ChatML
        )
        load_ms = (time.perf_counter() - load_started) * 1000

        self.embed_llm = None
        embed_ms = 0.0
        if self.embed_model_path and self.embed_model_path.exists():
            logger.info(f"Загрузка embedding-модели: {self.embed_model_path.name}")
            embed_started = time.perf_counter()
            self.embed_llm = Llama(
                model_path=str(self.embed_model_path),
                n_ctx=512,
                n_threads=self.n_threads,
                n_gpu_layers=0,
                embedding=True,
                verbose=verbose,
            )
            embed_ms = (time.perf_counter() - embed_started) * 1000

        self.load_ms = load_ms
        logger.info(
            f"Локальная SLM загружена за {load_ms:.0f} мс "
            f"(n_ctx={self.n_ctx}, n_threads={self.n_threads}, n_gpu_layers={n_gpu_layers}"
            + (f", embed {embed_ms:.0f} мс)" if embed_ms else ")")
        )

    # --- Отмена ---

    @property
    def num_ctx(self) -> int:
        """Размер окна контекста модели в токенах."""
        return self.n_ctx

    def cancel_request(self) -> None:
        """Запрашивает отмену текущего стриминга."""
        self._cancel_requested = True

    @property
    def is_cancelled(self) -> bool:
        """True если запрос отменён."""
        return self._cancel_requested

    def reset_cancel(self) -> None:
        """Сбрасывает флаг отмены."""
        self._cancel_requested = False

    # --- Основной чат ---

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[Any] | None = None,
        options: Any | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        """Генерирует ответ через llama.cpp с поддержкой JSON-tool-calling."""
        self.reset_cancel()

        # Формируем сообщения для llama.cpp (конвертация + системные инструкции)
        chat_messages = self._prepare_messages(messages, tools)

        # Параметры генерации
        temperature = _DEFAULT_TEMPERATURE
        max_tokens = self.max_tokens
        if options:
            temperature = getattr(options, "temperature", _DEFAULT_TEMPERATURE)
            max_tokens = getattr(options, "num_predict", self.max_tokens)

        # Обрезаем историю/контекст, чтобы промпт поместился в окно модели
        # вместе с ответом. Иначе llama.cpp не сможет декодировать промпт.
        chat_messages = self._fit_messages(chat_messages, reserve_tokens=max_tokens + 64)

        # Запускаем генерацию в отдельном потоке
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[ChatChunk | None] = asyncio.Queue()

        def _safe_put(chunk: ChatChunk) -> None:
            # Loop уже закрыт — просто игнорируем
            with contextlib.suppress(RuntimeError):
                asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)

        def _generate() -> None:
            """Синхронная генерация в отдельном потоке."""
            cancelled = False
            full_text_parts: list[str] = []
            prompt_chars = sum(len(str(m.get("content") or "")) for m in chat_messages)
            gen_started = time.perf_counter()
            first_token_at: float | None = None
            try:
                with self._lock:
                    response = self.llm.create_chat_completion(
                        # llama-cpp-python ждёт типизированные ChatCompletionRequest*Message,
                        # а мы собираем обычные dict. Контракт совпадает по полям, поэтому
                        # различие типов скрываем явно; при 4.8.A уйдёт вместе с MessagePart.
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
                        # ChatCompletionStreamResponse у llama-cpp-python - union, в котором
                        # часть вариантов является str, поэтому доступ к полям только через Any.
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
                    _safe_put(ChatChunk(content="", done=True))
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

                # Попытка распознать tool-call
                tool_call: ToolCall | None = None
                if tools and full_text.strip():
                    tool_call = self._parse_tool_call(full_text)

                if tool_call is not None:
                    logger.info(f"SLM: распознан tool_call → {tool_call.name}({tool_call.arguments})")
                    _safe_put(ChatChunk(content="", tool_calls=[tool_call], done=False))
                    _safe_put(ChatChunk(content="", done=True))
                else:
                    if full_text:
                        _safe_put(ChatChunk(content=full_text, done=False))
                    _safe_put(ChatChunk(content="", done=True))

            except Exception as e:
                logger.error(f"SLM generation error: {e}")
                _safe_put(ChatChunk(content=f"\n[Ошибка SLM: {e}]", done=True))

        thread = threading.Thread(target=_generate, daemon=True)
        thread.start()

        # Читаем из очереди и отдаём
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
            if chunk.done:
                break

    # --- Замеры производительности ---

    def _count_tokens(self, text: str) -> int:
        """Считает токены текста: токенизатором модели, иначе ~4 симв. на токен."""
        if not text:
            return 0
        try:
            return len(self.llm.tokenize(text.encode("utf-8"), add_bos=False))
        except Exception:
            return max(1, len(text) // 4)

    def _truncate_text(self, text: str, max_tokens: int) -> str:
        """Обрезает текст до max_tokens по токенизатору модели."""
        if max_tokens <= 0:
            return ""
        if self._count_tokens(text) <= max_tokens:
            return text
        try:
            tokens = self.llm.tokenize(text.encode("utf-8"), add_bos=False)
            trimmed = self.llm.detokenize(tokens[:max_tokens]).decode("utf-8", errors="replace")
            return trimmed + "\n… [контекст обрезан]"
        except Exception:
            return text[: max(64, max_tokens * 2)] + "\n… [контекст обрезан]"

    def _fit_messages(
        self,
        messages: list[dict[str, Any]],
        reserve_tokens: int,
    ) -> list[dict[str, Any]]:
        """Обрезает промпт под окно контекста модели.

        Системный промпт (с инструкцией по инструментам) и самые свежие
        сообщения важнее старых: сначала выбрасываем середину диалога, затем
        при необходимости подрезаем системный промпт и последнее сообщение.

        Args:
            messages: Сообщения в формате llama.cpp.
            reserve_tokens: Сколько токенов оставить на генерируемый ответ.

        Returns:
            Список сообщений, помещающийся в ``n_ctx``.
        """
        if not messages:
            return messages

        budget = max(256, self.n_ctx - reserve_tokens)
        if sum(self._count_tokens(str(m.get("content") or "")) for m in messages) <= budget:
            return messages

        system = messages[0] if messages[0].get("role") == "system" else None
        tail = messages[1:] if system is not None else list(messages)
        head: list[dict[str, Any]] = []

        system_tokens = 0
        if system is not None:
            system_tokens = self._count_tokens(str(system.get("content") or ""))
            # Системе отдаём не больше 60% бюджета: остальное — история и ответ.
            max_system = int(budget * 0.6)
            if system_tokens > max_system:
                system = dict(system)
                system["content"] = self._truncate_text(str(system.get("content") or ""), max_system)
                system_tokens = self._count_tokens(str(system["content"]))
            head = [system]

        used = system_tokens
        kept: list[dict[str, Any]] = []
        for msg in reversed(tail):
            tokens = self._count_tokens(str(msg.get("content") or ""))
            if used + tokens > budget:
                if kept:
                    break
                # Даже последнее сообщение не влезло — подрезаем его.
                msg = dict(msg)
                msg["content"] = self._truncate_text(str(msg.get("content") or ""), max(64, budget - used))
                tokens = self._count_tokens(str(msg["content"]))
            kept.append(msg)
            used += tokens
        kept.reverse()

        result = head + kept
        logger.debug(
            f"SLM: контекст обрезан {len(messages)} → {len(result)} сообщений "
            f"(~{used}/{budget} токенов, окно {self.n_ctx})"
        )
        return result

    @staticmethod
    def _make_stats(
        prompt_chars: int,
        tokens: int,
        gen_ms: float,
        ttft_ms: float | None,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        """Собирает метрики одного запроса."""
        seconds = gen_ms / 1000
        return {
            "prompt_chars": prompt_chars,
            "tokens": tokens,
            "gen_ms": round(gen_ms),
            "ttft_ms": round(ttft_ms) if ttft_ms is not None else None,
            "tokens_per_sec": round(tokens / seconds, 1) if seconds > 0 and tokens else 0.0,
            "cancelled": cancelled,
        }

    # --- Tool-call parsing ---

    def _parse_tool_call(self, text: str) -> ToolCall | None:
        """Пытается распарсить JSON-вызов инструмента из ответа модели."""
        stripped = text.strip()

        # 1. Убираем markdown-фенсы ```json ... ```
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
        if m:
            stripped = m.group(1).strip()

        # 2. Пробуем распарсить целиком
        parsed = self._try_json(stripped)
        if parsed is not None:
            return parsed

        # 3. Ищем первое вхождение JSON-объекта с "name"
        #    (аккуратный поиск по сбалансированным скобкам)
        for start, end in self._iter_json_spans(stripped):
            candidate = stripped[start:end]
            parsed = self._try_json(candidate)
            if parsed is not None:
                return parsed

        return None

    @staticmethod
    def _try_json(text: str) -> ToolCall | None:
        """Парсит JSON и, если это tool-call, возвращает ToolCall."""
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

        if not isinstance(parsed, dict):
            return None

        name: Any = None
        args: Any = None

        if "name" in parsed:
            name = parsed.get("name")
            args = parsed.get("arguments", {})
        elif isinstance(parsed.get("function"), dict):
            func = parsed["function"]
            name = func.get("name")
            args = func.get("arguments", {})

        if not isinstance(name, str) or not name:
            return None

        if not isinstance(args, dict):
            # Иногда модель отдаёт args как строку с JSON
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            else:
                args = {}

        return ToolCall(name=name, arguments=args)

    @staticmethod
    def _iter_json_spans(text: str) -> Iterator[tuple[int, int]]:
        """Возвращает (start, end) для каждого сбалансированного {...} блока."""
        stack: list[int] = []
        for i, ch in enumerate(text):
            if ch == "{":
                stack.append(i)
            elif ch == "}" and stack:
                start = stack.pop()
                if not stack:
                    yield start, i + 1

    # --- Инструкция для модели ---

    def _build_tool_instruction(self, tools: list[Any]) -> str:
        """Формирует инструкцию по вызову инструментов.

        Ключевая идея: даём ПРАВИЛО + разнообразные примеры одной задачи,
        чтобы модель обобщала паттерн, а не зазубривала «слово → инструмент».
        """
        lines = [
            "У тебя есть доступ к ИНСТРУМЕНТАМ для получения актуальной информации.",
            "",
            "ГЛАВНОЕ ПРАВИЛО:",
            "Прежде чем ответить, спроси себя: «Есть ли у меня актуальные данные?",
            "Могли ли они устареть?». Если речь идёт о чём-то, что меняется",
            "со временем — версии, ставки, курсы, новости, погода, цены,",
            "события, законы, релизы — СНАЧАЛА вызови web_search с точным",
            "запросом, ПОТОМ отвечай на основе полученных данных.",
            "",
            "НЕ отвечай «я не могу получить данные» или «поищите сами» —",
            "это твоя работа. Вызывай инструмент самостоятельно.",
            "",
            "ФОРМАТ ВЫЗОВА — ТОЛЬКО JSON, без текста вокруг:",
            '{"name": "имя_инструмента", "arguments": {"параметр": "значение"}}',
            "",
            "ПРИМЕРЫ (разные формулировки одной задачи — обобщай паттерн):",
            '  "Какая погода в Москве?"         → web_search("погода в Москве сегодня")',
            '  "Какая ключевая ставка ЦБ?"       → web_search("ключевая ставка ЦБ РФ сегодня")',
            '  "Актуальная версия GitLab Runner?" → web_search("gitlab runner latest version 2026")',
            '  "События в Москве сегодня"         → web_search("новости Москвы сегодня")',
            '  "Курс доллара к рублю"             → web_search("курс доллара к рублю сегодня")',
            "",
            "КОГДА ИНСТРУМЕНТ НЕ НУЖЕН:",
            '  "Расскажи про SOLID"               → (ответ из знаний)',
            '  "Что такое list comprehension?"     → (ответ из знаний)',
            '  "Привет"                           → (обычный текстовый ответ)',
            "",
            "Если инструмент вернул ошибку — ЧЕСТНО сообщи об этом. НЕ выдумывай цифры.",
            "",
            "ДОСТУПНЫЕ ИНСТРУМЕНТЫ:",
        ]

        for t in tools:
            func = getattr(t, "function", None) or t
            if not isinstance(func, dict):
                continue
            name = func.get("name", "")
            desc = func.get("description", "")
            params = func.get("parameters", {}) or {}
            props = params.get("properties", {}) or {}
            required = params.get("required", []) or []

            args_parts = []
            for pk, pv in props.items():
                ptype = pv.get("type", "any") if isinstance(pv, dict) else "any"
                marker = "" if pk in required else "?"
                args_parts.append(f"{pk}{marker}: {ptype}")
            args_str = ", ".join(args_parts) or "без аргументов"
            lines.append(f"- {name}({args_str}): {desc}")

        return "\n".join(lines)

    # --- Конвертация сообщений ---

    def _prepare_messages(self, messages: list[ChatMessage], tools: list[Any] | None) -> list[dict[str, Any]]:
        """Собирает сообщения для llama.cpp: конвертация + системные инструкции.

        Инструкция по инструментам и языковая директива вставляются в начало
        системного сообщения — при обрезке контекста ``_fit_messages`` сохраняет
        голову промпта, поэтому они не потеряются.
        """
        chat_messages = self._convert_messages(messages)
        if tools:
            chat_messages = self._prepend_system(chat_messages, self._build_tool_instruction(tools))
        return self._prepend_system(chat_messages, _LANGUAGE_INSTRUCTION)

    @staticmethod
    def _prepend_system(chat_messages: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
        """Добавляет текст в начало системного сообщения (или создаёт его)."""
        if chat_messages and chat_messages[0].get("role") == "system":
            chat_messages[0]["content"] = text + "\n\n" + str(chat_messages[0]["content"])
        else:
            chat_messages.insert(0, {"role": "system", "content": text})
        return chat_messages

    def _convert_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Конвертирует ChatMessage в формат llama.cpp.

        - tool-сообщения превращаем в user (ChatML корректно обрабатывает
          результаты инструмента как «внешний источник»).
        - tool_calls от ассистента разворачиваем в текст JSON — так модель
          видит «свой» прошлый вызов.
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                prefix = f"[Результат инструмента {msg.name or ''}]"
                content = f"{prefix}\n{msg.content or ''}".strip()
                result.append({"role": "user", "content": content})
            elif msg.role == "assistant" and msg.tool_calls:
                calls_json = ", ".join(
                    json.dumps(
                        {"name": tc.name, "arguments": tc.arguments},
                        ensure_ascii=False,
                    )
                    for tc in msg.tool_calls
                )
                content = (msg.content or "").strip()
                if content:
                    content = f"{content}\n{calls_json}"
                else:
                    content = calls_json
                result.append({"role": "assistant", "content": content})
            else:
                result.append({"role": msg.role, "content": msg.content or ""})
        return result

    # --- Эмбеддинги ---

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Получает эмбеддинги через локальную модель.

        Если embedding-модель не загружена — возвращает пустой список
        (RAG будет отключён).
        """
        if self.embed_llm is None:
            logger.warning("Embedding-модель не загружена, RAG недоступен")
            return []

        # Локальная переменная, а не self.embed_llm: внутри вложенной функции mypy
        # не сужает тип атрибута, проверенный выше.
        embed_llm = self.embed_llm

        def _embed() -> list[list[float]]:
            return [list(embed_llm.embed(t)) for t in texts]

        return await asyncio.to_thread(_embed)

    def check_tools_support(self, model: str) -> bool:
        """SLM поддерживает инструменты через JSON-парсинг."""
        return True

    async def __aenter__(self) -> "LocalLLMClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        return None
