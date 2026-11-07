"""Rule-based обработчик простых запросов без LLM."""

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any

from loguru import logger

from agents.ollama_client import ChatChunk, ChatMessage, ChatOptions


class RuleBasedFallback:
    """Обработчик простых запросов через регулярные выражения.

    Работает без LLM: погода, время, дата, простые вычисления.
    """

    WEATHER_PATTERN = re.compile(
        r'(?:погод[аыу]?|температур[аыу]?|сколько\s+градусов)'
        r'\s+(?:в|во|на)\s+([А-Яа-яЁё\-\s]+?)(?:\s|$|\?|!)',
        re.IGNORECASE,
    )

    TIME_PATTERN = re.compile(
        r'котор(?:ый|ое)\s+час|сколько\s+времени|текущее\s+время',
        re.IGNORECASE,
    )

    DATE_PATTERN = re.compile(
        r'како(?:е|й)\s+(?:сегодня\s+)?(?:число|день|дата)',
        re.IGNORECASE,
    )

    def __init__(self, weather_tool: Callable[[str], Awaitable[str]] | None = None) -> None:
        """Инициализация.

        Args:
            weather_tool: Callable для получения погоды (async)
        """
        self._weather_tool = weather_tool
        self._cancel_requested = False

    def cancel_request(self) -> None:
        """Отменяет запрос."""
        self._cancel_requested = True

    @property
    def is_cancelled(self) -> bool:
        """True если запрос отменён."""
        return self._cancel_requested

    def reset_cancel(self) -> None:
        """Сбрасывает флаг отмены."""
        self._cancel_requested = False

    async def chat(
        self,
        model: str = "",
        messages: list[ChatMessage] | None = None,
        tools: list | None = None,
        options: ChatOptions | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        """Обрабатывает запрос через правила."""
        if not messages:
            yield ChatChunk(content="Не понял запрос.", done=True)
            return

        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        if not last_user:
            yield ChatChunk(content="Не понял запрос.", done=True)
            return

        text = last_user.content or ""

        # Погода
        weather_match = self.WEATHER_PATTERN.search(text)
        if weather_match:
            city = weather_match.group(1).strip().rstrip("?!.")
            async for chunk in self._handle_weather(city):
                yield chunk
            return

        # Время
        if self.TIME_PATTERN.search(text):
            now = datetime.now()
            yield ChatChunk(
                content=f"Сейчас {now.strftime('%H:%M')}.",
                done=True,
            )
            return

        # Дата
        if self.DATE_PATTERN.search(text):
            now = datetime.now()
            months = [
                "",
                "января",
                "февраля",
                "марта",
                "апреля",
                "мая",
                "июня",
                "июля",
                "августа",
                "сентября",
                "октября",
                "ноября",
                "декабря",
            ]
            days = [
                "понедельник",
                "вторник",
                "среда",
                "четверг",
                "пятница",
                "суббота",
                "воскресенье",
            ]
            yield ChatChunk(
                content=(f"Сегодня {now.day} {months[now.month]} {now.year} года, {days[now.weekday()]}."),
                done=True,
            )
            return

        # Не распознано
        yield ChatChunk(
            content=(
                "Я могу ответить на простые вопросы (погода, время, дата) "
                "без языковой модели.\n\n"
                "Для сложных запросов:\n"
                "• Запустите Ollama — откройте терминал и выполните `ollama serve`\n"
                "• Или дождитесь загрузки встроенной модели\n\n"
                "После этого я смогу помогать с кодом и другими задачами."
            ),
            done=True,
        )

    async def _handle_weather(self, city: str) -> AsyncIterator[ChatChunk]:
        """Обрабатывает запрос погоды через инструмент."""
        yield ChatChunk(content=f"Сейчас узнаю погоду в {city}...\n", done=False)

        if self._weather_tool is None:
            yield ChatChunk(
                content="Погодный сервис недоступен.",
                done=True,
            )
            return

        try:
            result = await self._weather_tool(city)
            yield ChatChunk(content=result, done=True)
        except Exception as e:
            logger.error(f"Ошибка получения погоды: {e}")
            yield ChatChunk(
                content=f"Не удалось получить погоду: {e}",
                done=True,
            )

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Rule-based fallback не поддерживает эмбеддинги."""
        return []

    def check_tools_support(self, model: str) -> bool:
        """Rule-based fallback не поддерживает инструменты."""
        return False

    async def __aenter__(self) -> "RuleBasedFallback":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        return None
