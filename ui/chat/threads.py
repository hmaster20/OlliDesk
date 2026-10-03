"""Фоновые потоки для панели чата."""

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from loguru import logger
from PySide6.QtCore import QThread, Signal

from agents.agent_loop import (
    AgentContext,
    AgentIterationLimitError,
    AgentLoop,
)
from agents.download_slm import download_chat_model, download_embed_model
from agents.local_llm_client import LocalLLMClient
from agents.model_router import ModelRouter
from agents.ollama_client import ChatMessage, OllamaClient
from agents.tool_registry import ToolRegistry
from core.config import AgentMode
from core.exceptions import ModelNotFoundError, OllamaConnectionError


class OllamaChatThread(QThread):
    """Поток для асинхронного общения с Ollama (режим Chat, legacy)."""

    chunk_received = Signal(str)
    thinking_received = Signal(str)
    finish_received = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        model: str,
        messages: list[ChatMessage],
        base_url: str = "http://localhost:11434",
    ):
        super().__init__()
        self.model = model
        self.messages = messages
        self.base_url = base_url
        self._client: OllamaClient | None = None

    def cancel(self):
        if self._client:
            self._client.cancel_request()

    def run(self):
        asyncio.run(self._run_chat())

    async def _run_chat(self):
        try:
            self._client = OllamaClient(base_url=self.base_url)
            async with self._client as client:
                async for chunk in client.chat(
                    model=self.model,
                    messages=self.messages,
                    stream=True,
                ):
                    if chunk.reasoning_content:
                        self.thinking_received.emit(chunk.reasoning_content)
                    if chunk.content:
                        self.chunk_received.emit(chunk.content)
                    if chunk.done:
                        self.finish_received.emit()
        except OllamaConnectionError as e:
            self.error_occurred.emit(str(e))
        except ModelNotFoundError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"Неизвестная ошибка: {e}")


class AgentChatThread(QThread):
    """Поток для выполнения цикла агента (режимы Plan / Agent)."""

    chunk_received = Signal(str)
    thinking_received = Signal(str)
    tool_call_detected = Signal(str, str, str)
    tool_executed = Signal(str, str, bool)
    tool_started = Signal(str, str)
    finish_received = Signal(str)
    error_occurred = Signal(str)
    iteration_changed = Signal(int, int)
    tools_status_changed = Signal(bool)

    def __init__(
        self,
        client: OllamaClient,
        registry: ToolRegistry,
        model: str,
        context: AgentContext,
        messages_history: list[ChatMessage],
        agent_mode: AgentMode,
        max_iterations: int = 10,
    ):
        super().__init__()
        self.client = client
        self.registry = registry
        self.model = model
        self.context = context
        self.messages_history = messages_history
        self.agent_mode = agent_mode
        self.max_iterations = max_iterations

        self._tool_response: tuple[bool, dict[str, Any] | None] | None = None
        self._tool_event = threading.Event()

    def respond_to_tool(self, approved: bool, arguments: dict | None = None):
        self._tool_response = (approved, arguments)
        self._tool_event.set()

    async def _on_tool_request(
        self, name: str, description: str, arguments: dict
    ) -> bool:
        args_json = json.dumps(arguments, ensure_ascii=False)
        self.tool_call_detected.emit(name, description, args_json)

        self._tool_event.clear()
        await asyncio.get_event_loop().run_in_executor(None, self._tool_event.wait)

        approved, modified_args = self._tool_response or (False, None)
        if modified_args:
            arguments.clear()
            arguments.update(modified_args)
        return approved

    def run(self):
        asyncio.run(self._run_agent())

    async def _run_agent(self):
        self.tools_status_changed.emit(True)

        async def _notify_tools_disabled():
            self.tools_status_changed.emit(False)

        async def _on_tool_start(name: str, args: dict):
            args_json = json.dumps(args, ensure_ascii=False)
            self.tool_started.emit(name, args_json)

        async def _run_with(client):
            loop = AgentLoop(client=client, registry=self.registry, model=self.model)
            return await loop.run(
                context=self.context,
                messages_history=self.messages_history,
                on_token=self._on_token,
                on_tool_request=self._on_tool_request,
                max_iterations=self.max_iterations,
                on_thinking=self._on_thinking,
                on_tools_disabled=_notify_tools_disabled,
                on_tool_start=_on_tool_start,
            )

        try:
            if hasattr(self.client, "__aenter__"):
                async with self.client as client:
                    result = await _run_with(client)
            else:
                result = await _run_with(self.client)

            self.finish_received.emit(result)
        except AgentIterationLimitError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"Ошибка агента: {e}")

    async def _on_token(self, token: str):
        self.chunk_received.emit(token)

    async def _on_thinking(self, token: str):
        self.thinking_received.emit(token)


class RouterChatThread(QThread):
    """Чат через ModelRouter с авто-исполнением инструментов."""

    chunk_received = Signal(str)
    thinking_received = Signal(str)
    tool_started = Signal(str, str)
    tool_executed = Signal(str, str, bool)
    finish_received = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        router: ModelRouter,
        model: str,
        messages: list[ChatMessage],
        registry: ToolRegistry | None = None,
        project_root: str = "",
        vector_store: Any = None,
        search_cache: Any = None,
        max_iterations: int = 4,
    ):
        super().__init__()
        self.router = router
        self.model = model
        self.messages = messages
        self.registry = registry
        self.project_root = project_root
        self.vector_store = vector_store
        self.search_cache = search_cache
        self.max_iterations = max_iterations

    def cancel(self):
        self.router.cancel_request()

    def run(self):
        asyncio.run(self._run_chat())

    async def _run_chat(self):
        try:
            if self.registry is None:
                await self._stream_simple()
                return

            schemas = self.registry.get_schemas(AgentMode.CHAT)
            if not schemas:
                await self._stream_simple()
                return

            await self._stream_with_tools(schemas)
        except Exception as e:
            logger.exception("RouterChatThread error")
            self.error_occurred.emit(f"Ошибка: {e}")

    async def _stream_simple(self):
        async for chunk in self.router.chat(
            model=self.model, messages=self.messages, stream=True
        ):
            if chunk.reasoning_content:
                self.thinking_received.emit(chunk.reasoning_content)
            if chunk.content:
                self.chunk_received.emit(chunk.content)
            if chunk.done:
                self.finish_received.emit()

    async def _stream_with_tools(self, schemas: list):
        messages = list(self.messages)

        for _iteration in range(self.max_iterations):
            current_text = ""
            pending_tool_calls: list[Any] = []

            async for chunk in self.router.chat(
                model=self.model,
                messages=messages,
                tools=schemas,
                stream=True,
            ):
                if chunk.reasoning_content:
                    self.thinking_received.emit(chunk.reasoning_content)
                if chunk.content:
                    current_text += chunk.content
                    self.chunk_received.emit(chunk.content)
                if chunk.tool_calls:
                    pending_tool_calls.extend(chunk.tool_calls)

            if not pending_tool_calls:
                self.finish_received.emit()
                return

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=current_text,
                    tool_calls=pending_tool_calls,
                )
            )

            for tc in pending_tool_calls:
                args_json = json.dumps(tc.arguments, ensure_ascii=False)
                self.tool_started.emit(tc.name, args_json)

                extra: dict[str, Any] = {}
                if self.project_root:
                    extra["project_root"] = self.project_root
                if self.vector_store is not None:
                    extra["vector_store"] = self.vector_store
                if self.search_cache is not None:
                    extra["search_cache"] = self.search_cache

                result = await self.registry.execute(tc, **extra)
                self.tool_executed.emit(result.name, result.content, result.is_error)

                messages.append(
                    ChatMessage(
                        role="tool",
                        content=result.content,
                        name=result.name,
                    )
                )

        self.finish_received.emit()


class RagSearchThread(QThread):
    """Поток для асинхронного RAG-поиска."""

    results_ready = Signal(str, str)
    search_error = Signal(str)

    def __init__(self, vector_store, query: str, n_results: int = 5):
        super().__init__()
        self.vector_store = vector_store
        self.query = query
        self.n_results = n_results

    def run(self):
        try:
            asyncio.run(self._search())
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                self.search_error.emit(str(e))
            else:
                raise

    async def _search(self):
        try:
            results = await self.vector_store.search(
                self.query, n_results=self.n_results
            )
            if results:
                context_parts = []
                source_parts = []
                for r in results[: self.n_results]:
                    context_parts.append(
                        f"### Файл: {r.file_path} "
                        f"(строки {r.chunk.start_line}-{r.chunk.end_line})\n"
                        f"```\n{r.chunk.content}\n```"
                    )
                    source_parts.append(
                        f"{r.file_path}:{r.chunk.start_line}-{r.chunk.end_line}"
                    )

                context = "\n\n".join(context_parts)
                sources = ", ".join(source_parts[:3])
                self.results_ready.emit(context, sources)
            else:
                self.results_ready.emit("", "")
        except Exception as e:
            self.search_error.emit(str(e))


class _OllamaCheckThread(QThread):
    """Проверяет доступность Ollama в фоне."""

    result = Signal(bool)

    def __init__(self, client: OllamaClient):
        super().__init__()
        self.client = client

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            ok = loop.run_until_complete(self.client.is_available())
            loop.close()
            self.result.emit(ok)
        except Exception as e:
            logger.debug(f"Ollama check failed: {e}")
            self.result.emit(False)


class _SLMLoadThread(QThread):
    """Загружает SLM в фоне."""

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, chat_path: Path, embed_path: Path | None):
        super().__init__()
        self.chat_path = chat_path
        self.embed_path = embed_path

    def run(self):
        try:
            client = LocalLLMClient(
                model_path=self.chat_path,
                embed_model_path=self.embed_path,
                n_ctx=4096,
                n_threads=4,
            )
            self.finished.emit(client)
        except Exception as e:
            logger.exception("Ошибка загрузки SLM")
            self.error.emit(str(e))


class _SLMDownloadThread(QThread):
    """Скачивает модели SLM в фоне."""

    finished = Signal(str, str)
    error = Signal(str)

    def run(self):
        try:
            chat = download_chat_model()
            embed = download_embed_model()
            self.finished.emit(str(chat), str(embed) if embed else "")
        except Exception as e:
            logger.exception("Ошибка скачивания SLM")
            self.error.emit(str(e))


class WebSearchThread(QThread):
    """Поток для интернет-поиска и генерации ответа через router."""

    chunk_received = Signal(str)
    thinking_received = Signal(str)
    finish_received = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        router: ModelRouter,
        model: str,
        query: str,
        messages_history: list[ChatMessage],
        search_cache=None,
    ):
        super().__init__()
        self.router = router
        self.model = model
        self.query = query
        self.messages_history = messages_history
        self.search_cache = search_cache

    def run(self):
        asyncio.run(self._run())

    async def _run(self):
        try:
            from tools.web_search import web_search

            search_results = await web_search(
                query=self.query,
                max_results=5,
                search_cache=self.search_cache,
            )

            if not search_results or "Ничего не найдено" in search_results:
                self.error_occurred.emit(
                    "Поиск не дал результатов. Попробуйте уточнить запрос."
                )
                return

            system_prompt = (
                "Ты — поисковый ассистент. Используй результаты поиска для ответа. "
                "Отвечай на русском языке, будь лаконичен и полезен."
            )
            user_content = f"Вопрос: {self.query}\n\nРезультаты поиска:\n{search_results}"

            messages = [
                ChatMessage(role="system", content=system_prompt),
                *self.messages_history,
                ChatMessage(role="user", content=user_content),
            ]

            async for chunk in self.router.chat(
                model=self.model, messages=messages, stream=True
            ):
                if chunk.reasoning_content:
                    self.thinking_received.emit(chunk.reasoning_content)
                if chunk.content:
                    self.chunk_received.emit(chunk.content)
                if chunk.done:
                    self.finish_received.emit()

        except Exception as e:
            self.error_occurred.emit(f"Ошибка: {e}")