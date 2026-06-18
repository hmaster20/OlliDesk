"""Потоки для асинхронного общения с Ollama."""

import asyncio
import threading
from PySide6.QtCore import QThread, Signal

from agents.agent_loop import AgentContext, AgentLoop
from agents.ollama_client import ChatMessage, OllamaClient
from agents.tool_registry import ToolRegistry
from core.config import AgentMode
from core.exceptions import ModelNotFoundError, OllamaConnectionError


class OllamaChatThread(QThread):
    """Поток для обычного чата."""
    chunk_received = Signal(str)
    finish_received = Signal()
    error_occurred = Signal(str)

    def __init__(self, model: str, messages: list[ChatMessage], base_url: str):
        super().__init__()
        self.model = model
        self.messages = messages
        self.base_url = base_url
        self._client = None

    def cancel(self):
        if self._client:
            self._client.cancel_request()

    def run(self):
        asyncio.run(self._run())

    async def _run(self):
        try:
            self._client = OllamaClient(base_url=self.base_url)
            async with self._client as client:
                async for chunk in client.chat(
                    model=self.model,
                    messages=self.messages,
                    stream=True
                ):
                    if chunk.content:
                        self.chunk_received.emit(chunk.content)
                    if chunk.done:
                        self.finish_received.emit()
        except (OllamaConnectionError, ModelNotFoundError) as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(f"Неизвестная ошибка: {e}")


class AgentChatThread(QThread):
    """Поток для агента."""
    chunk_received = Signal(str)
    tool_call_detected = Signal(str, str, str)  # name, description, args_json
    finish_received = Signal()
    error_occurred = Signal(str)

    def __init__(self, client: OllamaClient, registry: ToolRegistry, model: str,
                 context: AgentContext, messages_history: list[ChatMessage],
                 agent_mode: AgentMode, max_iterations: int = 10):
        super().__init__()
        self.client = client
        self.registry = registry
        self.model = model
        self.context = context
        self.messages_history = messages_history
        self.agent_mode = agent_mode
        self.max_iterations = max_iterations
        self._tool_response = None
        self._tool_event = threading.Event()

    def respond_to_tool(self, approved: bool, arguments: dict = None):
        self._tool_response = (approved, arguments)
        self._tool_event.set()

    async def _on_tool_request(self, name: str, description: str, arguments: dict) -> bool:
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
        asyncio.run(self._run())

    async def _run(self):
        async def on_token(token: str):
            self.chunk_received.emit(token)

        try:
            async with self.client as client:
                loop = AgentLoop(client, self.registry, self.model)
                result = await loop.run(
                    context=self.context,
                    messages_history=self.messages_history,
                    on_token=on_token,
                    on_tool_request=self._on_tool_request,
                    max_iterations=self.max_iterations,
                )
            self.finish_received.emit()
        except Exception as e:
            self.error_occurred.emit(str(e))
