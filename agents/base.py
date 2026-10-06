"""Базовые Protocol-контракты для LLM-бэкендов."""

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Protocol, runtime_checkable

from agents.ollama_client import ChatChunk, ChatMessage, ChatOptions, ToolSchema


@runtime_checkable
class LLMBackend(Protocol):
    """Минимальный контракт бэкенда чата.

    Содержит минимальный набор методов, который реально используется AgentLoop
    и UI-потоками. Инварианты отражают текущее поведение трёх реализаций
    и ModelRouter.
    """

    def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None = None,
        options: ChatOptions | None = None,
        stream: bool = True,
    ) -> AsyncIterator[ChatChunk]:
        """Стриминг ответа через клиента LLM.

        Реализации могут бросать:
        - ToolsNotSupportedError — при запросе инструментов у модели, которая их не поддерживает
        """
        ...

    def cancel_request(self) -> None:
        """Запросить отмену текущего стрима/запроса."""
        ...

    @property
    def is_cancelled(self) -> bool:
        """True, если текущий запрос отменён."""
        ...

    def reset_cancel(self) -> None:
        """Сбрасывает флаг отмены текущего запроса."""
        ...

    async def __aenter__(self) -> "LLMBackend":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None
