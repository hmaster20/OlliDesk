"""Маршрутизатор запросов между бэкендами LLM."""

from collections.abc import AsyncIterator
from enum import Enum, auto

from loguru import logger

from agents.ollama_client import ChatChunk, ChatMessage, OllamaClient


class BackendType(Enum):
    """Тип доступного бэкенда."""
    OLLAMA = auto()
    LOCAL_SLM = auto()
    RULE_BASED = auto()
    NONE = auto()


class ModelRouter:
    """Выбирает доступный бэкенд для LLM.

    Приоритет:
        1. Ollama (если сервер запущен)
        2. Встроенная SLM (llama.cpp)
        3. Rule-based fallback (без LLM)
    """

    def __init__(self):
        self._ollama: OllamaClient | None = None
        self._local_slm = None
        self._rule_based = None
        self._active_backend: BackendType = BackendType.NONE
        self._ollama_available = False
        self._slm_available = False

    def set_ollama(self, client: OllamaClient) -> None:
        """Устанавливает Ollama-клиент."""
        self._ollama = client

    def set_local_slm(self, client) -> None:
        """Устанавливает локальный SLM-клиент."""
        self._local_slm = client

    def set_rule_based(self, client) -> None:
        """Устанавливает rule-based fallback."""
        self._rule_based = client

    def set_ollama_available(self, available: bool) -> None:
        """Обновляет статус доступности Ollama."""
        self._ollama_available = available
        self._update_active()

    def set_slm_available(self, available: bool) -> None:
        """Обновляет статус доступности SLM."""
        self._slm_available = available
        self._update_active()

    def _update_active(self) -> None:
        """Обновляет активный бэкенд."""
        if self._ollama_available and self._ollama is not None:
            self._active_backend = BackendType.OLLAMA
        elif self._slm_available and self._local_slm is not None:
            self._active_backend = BackendType.LOCAL_SLM
        elif self._rule_based is not None:
            self._active_backend = BackendType.RULE_BASED
        else:
            self._active_backend = BackendType.NONE

        logger.info(f"Активный бэкенд LLM: {self._active_backend.name}")

    @property
    def active_backend(self) -> BackendType:
        """Текущий активный бэкенд."""
        return self._active_backend

    @property
    def can_use_tools(self) -> bool:
        """Может ли текущий бэкенд использовать инструменты."""
        return self._active_backend in (BackendType.OLLAMA, BackendType.LOCAL_SLM)

    @property
    def active_client(self):
        """Возвращает активный клиент."""
        if self._active_backend == BackendType.OLLAMA:
            return self._ollama
        if self._active_backend == BackendType.LOCAL_SLM:
            return self._local_slm
        return self._rule_based

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list | None = None,
        options=None,
        stream: bool = True,
    ) -> AsyncIterator[ChatChunk]:
        """Маршрутизирует запрос к активному бэкенду."""
        client = self.active_client
        if client is None:
            yield ChatChunk(
                content="Ни один бэкенд LLM не доступен. "
                        "Запустите Ollama или дождитесь загрузки локальной модели.",
                done=True,
            )
            return

        async for chunk in client.chat(
            model=model,
            messages=messages,
            tools=tools,
            options=options,
            stream=stream,
        ):
            yield chunk

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Маршрутизирует запрос эмбеддингов."""
        if self._active_backend == BackendType.OLLAMA and self._ollama:
            return await self._ollama.embed(model, texts)
        if self._active_backend == BackendType.LOCAL_SLM and self._local_slm:
            return await self._local_slm.embed(model, texts)
        return []

    def cancel_request(self) -> None:
        """Отменяет текущий запрос."""
        client = self.active_client
        if client and hasattr(client, "cancel_request"):
            client.cancel_request()

    @property
    def is_cancelled(self) -> bool:
        """True если запрос отменён."""
        client = self.active_client
        if client and hasattr(client, "is_cancelled"):
            return bool(client.is_cancelled)
        return False