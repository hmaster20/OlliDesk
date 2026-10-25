"""Маршрутизатор запросов между бэкендами LLM."""

from collections.abc import AsyncIterator
from enum import Enum, auto
from typing import Any

from loguru import logger

from agents.base import LLMBackend
from agents.ollama_client import ChatChunk, ChatMessage, ChatOptions
from core.context_budget import DEFAULT_NUM_CTX
from core.exceptions import EmbedderError


class BackendType(Enum):
    """Тип доступного бэкенда."""

    OLLAMA = auto()
    LOCAL_SLM = auto()
    RULE_BASED = auto()
    NONE = auto()


# Строковые имена бэкендов в конфиге → элементы BackendType.
_CONFIG_NAMES: dict[str, BackendType] = {
    "ollama": BackendType.OLLAMA,
    "local_slm": BackendType.LOCAL_SLM,
}


class ModelRouter:
    """Выбирает доступный бэкенд для LLM.

    Порядок предпочтения задаётся конфигом ``backends.priority`` (по умолчанию
    встроенная SLM, затем Ollama). Если ни один LLM-бэкенд не доступен,
    используется rule-based fallback.

    Эмбеддинги для RAG считаются отдельно от выбора чат-бэкенда: при
    ``backends.embed_backend = auto`` берётся активный бэкенд, а если он
    эмбеддинги посчитать не смог — остальные по очереди. Это позволяет
    работать без Ollama, используя локальную embedding-модель.
    """

    def __init__(self) -> None:
        self._ollama: LLMBackend | None = None
        self._local_slm: LLMBackend | None = None
        self._rule_based: LLMBackend | None = None
        self._active_backend: BackendType = BackendType.NONE
        self._ollama_available = False
        self._slm_available = False
        self._priority: list[BackendType] = [
            BackendType.OLLAMA,
            BackendType.LOCAL_SLM,
        ]
        self._embed_preference: BackendType | None = None
        self._enabled: set[BackendType] = {BackendType.LOCAL_SLM}

    def set_ollama(self, client: LLMBackend) -> None:
        """Устанавливает Ollama-клиент."""
        self._ollama = client

    def set_local_slm(self, client: LLMBackend | None) -> None:
        """Установить клиент локальной SLM-модели."""
        self._local_slm = client

    def set_rule_based(self, client: LLMBackend | None) -> None:
        """Установить клиент rule-based fallback."""
        self._rule_based = client

    def set_ollama_available(self, available: bool) -> None:
        """Обновляет статус доступности Ollama."""
        self._ollama_available = available
        self._update_active()

    def set_slm_available(self, available: bool) -> None:
        """Обновляет статус доступности SLM."""
        self._slm_available = available
        self._update_active()

    def set_enabled_backends(self, names: list[str]) -> None:
        """Задаёт whitelist бэкендов, которые разрешено активировать.

        Бэкенды не из этого списка игнорируются, даже если физически доступны
        (Ollama запущен, но пользователь его не включал).
        """
        self._enabled = {_CONFIG_NAMES[name] for name in names if name in _CONFIG_NAMES}
        self._enabled.add(BackendType.LOCAL_SLM)  # локальный режим — всегда
        logger.info(f"Разрешённые бэкенды: {[b.name for b in self._enabled]}")
        self._update_active()

    def _update_active(self) -> None:
        """Обновляет активный бэкенд с учётом whitelist."""
        for backend in self._priority:
            if backend not in self._enabled:
                continue  # пропускаем то, что не разрешено
            if backend is BackendType.OLLAMA and self._ollama_available and self._ollama:
                self._active_backend = BackendType.OLLAMA
                break
            if backend is BackendType.LOCAL_SLM and self._slm_available and self._local_slm:
                self._active_backend = BackendType.LOCAL_SLM
                break
        else:
            self._active_backend = (
                BackendType.RULE_BASED if self._rule_based is not None else BackendType.NONE
            )
        logger.info(f"Активный бэкенд LLM: {self._active_backend.name}")

    def set_priority(self, names: list[str]) -> None:
        """Задаёт порядок предпочтения бэкендов для чата.

        Args:
            names: Список строк из конфига (``ollama``, ``local_slm``).
                Неизвестные имена игнорируются, дубликаты схлопываются,
                rule-based всегда идёт последним.
        """
        order: list[BackendType] = []
        for name in names:
            backend = _CONFIG_NAMES.get(name)
            if backend is not None and backend not in order:
                order.append(backend)
        if not order:
            order = [BackendType.OLLAMA, BackendType.LOCAL_SLM]
        self._priority = order
        logger.info(f"Порядок бэкендов: {[b.name for b in order]} + RULE_BASED")
        self._update_active()

    def set_embed_backend(self, name: str) -> None:
        """Задаёт, кто считает эмбеддинги для RAG.

        Args:
            name: ``auto`` — следовать активному чат-бэкенду с fallback на
                остальные; ``ollama`` или ``local_slm`` — только этот бэкенд.
        """
        self._embed_preference = None if name == "auto" else _CONFIG_NAMES.get(name)
        logger.info(
            "Эмбеддинги: auto (по активному бэкенду)"
            if self._embed_preference is None
            else f"Эмбеддинги: {self._embed_preference.name}"
        )

    def _client_for(self, backend: BackendType) -> Any:
        """Клиент для указанного типа бэкенда, если он доступен."""
        if backend is BackendType.OLLAMA and self._ollama is not None:
            return self._ollama
        if backend is BackendType.LOCAL_SLM and self._local_slm is not None:
            return self._local_slm
        return None

    def _embed_order(self) -> list[BackendType]:
        """Порядок попыток для эмбеддингов.

        Явный `embed_backend` (``ollama`` / ``local_slm``) имеет приоритет
        над чат-whitelist ``_enabled``: пользователь мог выключить Ollama
        для чата, но оставить её для эмбеддингов. Пиннинг = «используй
        ровно этот бэкенд» (если клиент есть). При ``auto`` — старая
        логика с whitelist.
        """
        # Явный пиннинг: клиент есть → используем ровно его, независимо
        # от `_enabled`. Клиента нет → падаем на auto-логику ниже.
        if self._embed_preference is not None and self._client_for(self._embed_preference) is not None:
            return [self._embed_preference]

        available = [
            b
            for b in (BackendType.OLLAMA, BackendType.LOCAL_SLM)
            if b in self._enabled and self._client_for(b) is not None
        ]
        rest = [b for b in available if b is not self._active_backend]
        return [b for b in (self._active_backend,) if b in available] + rest

    @property
    def active_backend(self) -> BackendType:
        """Текущий активный бэкенд."""
        return self._active_backend

    @property
    def can_use_tools(self) -> bool:
        """Может ли текущий бэкенд использовать инструменты."""
        return self._active_backend in (BackendType.OLLAMA, BackendType.LOCAL_SLM)

    @property
    def active_client(self) -> Any:
        """Возвращает активный клиент."""
        if self._active_backend == BackendType.OLLAMA:
            return self._ollama
        if self._active_backend == BackendType.LOCAL_SLM:
            return self._local_slm
        return self._rule_based

    @property
    def num_ctx(self) -> int:
        """Окно контекста активного бэкенда в токенах.

        Нужно для расчёта бюджета прикреплённых файлов и RAG-контекста.
        У rule-based бэкенда окна нет — возвращается значение по умолчанию.
        """
        client = self.active_client
        value = getattr(client, "num_ctx", None)
        if isinstance(value, int) and value > 0:
            return value
        return DEFAULT_NUM_CTX

    async def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        tools: list | None = None,
        options: ChatOptions | None = None,
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
        """Считает эмбеддинги, перебирая доступные бэкенды по очереди.

        Args:
            model: Имя модели эмбеддингов (например, ``nomic-embed-text``)
            texts: Тексты для векторизации

        Returns:
            Список векторов — по одному на каждый текст

        Raises:
            EmbedderError: Ни один бэкенд не смог посчитать эмбеддинги.
                Бэкенд считается неудачным, если он вернул пустой результат
                или упал с исключением.
        """
        order = self._embed_order()
        if not order:
            raise EmbedderError(
                "Не удалось посчитать эмбеддинги: нет ни одного доступного "
                "бэкенда. Запустите Ollama или загрузите локальную SLM."
            )

        problems: list[str] = []
        for backend in order:
            client = self._client_for(backend)
            if client is None:
                continue
            try:
                vectors: list[list[float]] = await client.embed(model, texts)
            except Exception as e:
                problems.append(f"{backend.name}: {e}")
                logger.warning(f"Эмбеддинги через {backend.name} не удались: {e}")
                continue
            if vectors:
                if backend is not self._active_backend:
                    logger.info(
                        f"Эмбеддинги посчитаны через {backend.name} "
                        f"(активный чат-бэкенд — {self._active_backend.name})"
                    )
                return vectors
            problems.append(f"{backend.name}: пустой результат")

        raise EmbedderError("Не удалось посчитать эмбеддинги для RAG. " + "; ".join(problems))

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

    def reset_cancel(self) -> None:
        """Сбрасывает флаг отмены на активном бэкенде."""
        client = self.active_client
        if client and hasattr(client, "reset_cancel"):
            client.reset_cancel()
