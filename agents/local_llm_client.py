"""Клиент встроенной SLM через llama.cpp (fallback для Ollama)."""

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from loguru import logger

from agents.ollama_client import ChatChunk, ChatMessage, ToolCall


class LocalLLMClient:
    """Клиент локальной SLM через llama-cpp-python.

    Используется как fallback, когда Ollama недоступен.
    Поддерживает стриминг, простой tool-calling через JSON-парсинг
    и эмбеддинги (если загружена embedding-модель).
    """

    def __init__(
        self,
        model_path: str | Path,
        embed_model_path: str | Path | None = None,
        n_ctx: int = 4096,
        n_threads: int = 4,
        n_batch: int = 512,
        n_gpu_layers: int = 0,
        verbose: bool = False,
    ):
        """Инициализация клиента.

        Args:
            model_path: Путь к GGUF-файлу chat-модели
            embed_model_path: Путь к GGUF-файлу embedding-модели (опционально)
            n_ctx: Размер контекста в токенах
            n_threads: Количество потоков CPU
            n_batch: Размер батча для обработки
            n_gpu_layers: Количество слоёв на GPU (0 = CPU-only)
            verbose: Логирование llama.cpp
        """
        from llama_cpp import Llama

        self.model_path = Path(model_path)
        self.embed_model_path = Path(embed_model_path) if embed_model_path else None
        self.n_ctx = n_ctx
        self.n_threads = n_threads

        self._cancel_requested = False
        self._lock = threading.Lock()

        if not self.model_path.exists():
            raise FileNotFoundError(f"Модель не найдена: {self.model_path}")

        logger.info(f"Загрузка локальной SLM: {self.model_path.name}")
        self.llm = Llama(
            model_path=str(self.model_path),
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_batch=n_batch,
            n_gpu_layers=n_gpu_layers,
            verbose=verbose,
            chat_format="chatml",  # Qwen2.5 использует ChatML
        )

        self.embed_llm = None
        if self.embed_model_path and self.embed_model_path.exists():
            logger.info(f"Загрузка embedding-модели: {self.embed_model_path.name}")
            self.embed_llm = Llama(
                model_path=str(self.embed_model_path),
                n_ctx=512,
                n_threads=n_threads,
                n_gpu_layers=0,
                embedding=True,
                verbose=verbose,
            )

        logger.info("Локальная SLM загружена успешно")

    # --- Управление отменой ---

    def cancel_request(self):
        """Запрашивает отмену текущего стриминга."""
        self._cancel_requested = True

    @property
    def is_cancelled(self) -> bool:
        """True если запрос отменён."""
        return self._cancel_requested

    def reset_cancel(self):
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
        """Генерирует ответ через llama.cpp.

        Работает в отдельном потоке, чтобы не блокировать event loop.
        """
        self.reset_cancel()

        # Формируем сообщения для llama.cpp
        chat_messages = self._convert_messages(messages)

        # Если есть инструменты — добавляем инструкцию в system prompt
        if tools:
            tool_instruction = self._build_tool_instruction(tools)
            chat_messages.insert(0, {"role": "system", "content": tool_instruction})

        # Параметры генерации
        temperature = 0.7
        max_tokens = 1024
        if options:
            temperature = getattr(options, "temperature", 0.7)
            max_tokens = getattr(options, "num_predict", 1024)

        # Запускаем генерацию в отдельном потоке
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[ChatChunk | None] = asyncio.Queue()

        def _generate():
            """Синхронная генерация в отдельном потоке."""
            try:
                with self._lock:
                    response = self.llm.create_chat_completion(
                        messages=chat_messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stream=True,
                        stop=["<|im_end|>", "<|endoftext|>"],
                    )

                    for chunk in response:
                        if self._cancel_requested:
                            break

                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content", "")

                        if content:
                            asyncio.run_coroutine_threadsafe(
                                queue.put(ChatChunk(content=content, done=False)),
                                loop,
                            )

                asyncio.run_coroutine_threadsafe(
                    queue.put(ChatChunk(content="", done=True)),
                    loop,
                )
            except Exception as e:
                logger.error(f"Ошибка генерации SLM: {e}")
                asyncio.run_coroutine_threadsafe(
                    queue.put(ChatChunk(content=f"\n[Ошибка: {e}]", done=True)),
                    loop,
                )

        # Запускаем поток
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

    # --- Эмбеддинги ---

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Получает эмбеддинги через локальную модель.

        Если embedding-модель не загружена — возвращает пустой список
        (RAG будет отключён).
        """
        if self.embed_llm is None:
            logger.warning("Embedding-модель не загружена, RAG недоступен")
            return []

        def _embed():
            results = []
            for text in texts:
                emb = self.embed_llm.embed(text)
                results.append(list(emb))
            return results

        return await asyncio.to_thread(_embed)

    # --- Вспомогательные методы ---

    def _convert_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Конвертирует ChatMessage в формат llama.cpp."""
        result = []
        for msg in messages:
            entry: dict[str, Any] = {
                "role": msg.role,
                "content": msg.content or "",
            }
            if msg.role == "tool" and msg.name:
                entry["name"] = msg.name
            result.append(entry)
        return result

    def _build_tool_instruction(self, tools: list[Any]) -> str:
        """Формирует инструкцию по вызову инструментов для SLM.

        SLM не имеет нативного tool-calling, поэтому используем
        JSON-инструкцию в system prompt.
        """
        tool_descriptions = []
        for tool in tools:
            if hasattr(tool, "function"):
                func = tool.function
                name = func.get("name", "")
                desc = func.get("description", "")
                params = func.get("parameters", {})
                tool_descriptions.append(
                    f"- {name}: {desc}\n  Параметры: {params}"
                )

        tools_text = "\n".join(tool_descriptions)

        return (
            "Ты — AI-ассистент с доступом к инструментам.\n"
            "Если тебе нужно вызвать инструмент, ответь СТРОГО в формате JSON:\n"
            '{"name": "имя_инструмента", "arguments": {"параметр": "значение"}}\n'
            "Не добавляй ничего кроме JSON, если вызываешь инструмент.\n"
            "Если инструмент не нужен — просто ответь текстом.\n\n"
            f"Доступные инструменты:\n{tools_text}"
        )

    def check_tools_support(self, model: str) -> bool:
        """SLM поддерживает инструменты через JSON-парсинг."""
        return True