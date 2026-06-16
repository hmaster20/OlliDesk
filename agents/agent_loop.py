"""Цикл рассуждений и действий (ReAct) агента."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from agents.ollama_client import ChatMessage, OllamaClient, ToolCall
from agents.tool_registry import ToolRegistry
from core.config import AgentMode, ToolPolicy
from core.exceptions import OlliDeskError, ToolsNotSupportedError


class AgentIterationLimitError(OlliDeskError):
    """Достигнут лимит итераций агента."""


class AgentError(OlliDeskError):
    """Общая ошибка агента."""


@dataclass
class AgentContext:
    """Контекст для запуска агента."""
    system_prompt: str = ""
    rag_context: str = ""
    project_root: str = ""
    vector_store: object = None
    extra_rules: str = ""
    max_tokens: int = 4096
    mode: AgentMode = AgentMode.AGENT


def _build_system_prompt(context: AgentContext) -> str:
    """Собирает system-промпт из контекста."""
    mode_name = {AgentMode.PLAN: "План", AgentMode.AGENT: "Агент"}.get(context.mode, "Агент")

    parts = [
        "Ты — OlliDesk, автономный AI-ассистент для написания и рефакторинга кода.",
        "Отвечай на русском языке.",
        f"Текущий режим: {mode_name}.",
    ]

    if context.mode == AgentMode.PLAN:
        parts.append(
            "Тебе доступны read-only инструменты: чтение файлов, поиск в коде, "
            "поиск в интернете, просмотр статуса git. Используй их для сбора информации. "
            "Не выводи JSON-план — сразу вызывай нужные инструменты."
        )
    elif context.mode == AgentMode.AGENT:
        parts.append(
            "У тебя есть доступ ко всем инструментам, включая запись файлов и "
            "операции git. Используй их для выполнения задачи."
        )
        parts.append(
            "Перед записью в файл сперва прочитай его содержимое, чтобы понять текущее состояние."
        )

    if context.rag_context:
        parts.append(f"\nКонтекст из кодовой базы:\n{context.rag_context}")

    if context.extra_rules:
        parts.append(f"\nДополнительные правила:\n{context.extra_rules}")

    if context.project_root:
        parts.append(
            f"\nКорень проекта: {context.project_root}\n"
            f"Все пути к файлам должны быть относительными от корня проекта."
        )

    return "\n\n".join(parts)


def _create_tool_result_message(
    tool_call_id: str,
    name: str,
    content: str,
    is_error: bool = False,
) -> ChatMessage:
    """Создаёт сообщение с результатом выполнения инструмента."""
    prefix = "❌ Ошибка:\n" if is_error else ""
    return ChatMessage(
        role="tool",
        content=f"{prefix}{content}",
        tool_call_id=tool_call_id,
        name=name,
    )


class AgentLoop:
    """Цикл ReAct агента."""

    def __init__(self, client: OllamaClient, registry: ToolRegistry, model: str):
        """
        Args:
            client: Клиент Ollama
            registry: Реестр инструментов
            model: Имя модели
        """
        self.client = client
        self.registry = registry
        self.model = model

    async def run(
        self,
        context: AgentContext,
        messages_history: list[ChatMessage],
        on_token: Callable[[str], Awaitable[None]],
        on_tool_request: Callable[[str, str, dict[str, Any]], Awaitable[bool]],
        max_iterations: int = 10,
        on_thinking: Callable[[str], Awaitable[None]] | None = None,
        on_tools_disabled: Callable[[], Awaitable[None]] | None = None,
        on_tool_start: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> str:
        """Запускает цикл агента.

        Args:
            context: Контекст (RAG, project_root, векторное хранилище)
            messages_history: История сообщений для продолжения диалога (включает новое сообщение пользователя)
            on_token: Колбэк для каждого токена стрима
            on_tool_request: Колбэк запроса подтверждения (name, description, arguments) -> bool
            max_iterations: Максимальное число итераций
            on_tools_disabled: Колбэк при отключении инструментов
            on_tool_start: Колбэк при начале выполнения инструмента (name, arguments)

        Returns:
            Финальный текст ответа

        Raises:
            AgentIterationLimitError: При превышении лимита итераций
            AgentError: При других ошибках
        """
        messages = [ChatMessage(role="system", content=_build_system_prompt(context))]

        for msg in messages_history:
            messages.append(msg)

        tools_to_send: list | None = self.registry.get_schemas(context.mode)

        for iteration in range(max_iterations):
            logger.debug(f"Агент: итерация {iteration + 1}/{max_iterations}")

            current_text = ""
            pending_tool_calls = []

            try:
                stream = self.client.chat(
                    model=self.model,
                    messages=messages,
                    tools=tools_to_send,
                    stream=True,
                )

                async for chunk in stream:
                    if chunk.reasoning_content and on_thinking:
                        await on_thinking(chunk.reasoning_content)
                    if chunk.content:
                        current_text += chunk.content
                        await on_token(chunk.content)
                    if chunk.tool_calls:
                        pending_tool_calls.extend(chunk.tool_calls)

                if self.client.is_cancelled:
                    logger.info("Запрос к Ollama отменён пользователем")
                    return current_text or "Отменено"

            except ToolsNotSupportedError:
                logger.warning(f"Модель {self.model} не поддерживает инструменты, повтор без инструментов")
                tools_to_send = None
                if on_tools_disabled:
                    await on_tools_disabled()
                if current_text:
                    await on_token("\n\n")
                continue

            # Fallback: model may output JSON tool call as text (no native function calling)
            if not pending_tool_calls and current_text and tools_to_send:
                import json as _json
                stripped = current_text.strip()
                if stripped.startswith("{") and "name" in stripped and "arguments" in stripped:
                    try:
                        parsed = _json.loads(stripped)
                        tc = ToolCall(name=parsed["name"], arguments=parsed.get("arguments", {}))
                        pending_tool_calls = [tc]
                        current_text = ""
                    except (_json.JSONDecodeError, KeyError):
                        pass

            if pending_tool_calls and tools_to_send:
                messages.append(
                    ChatMessage(
                        role="assistant", content=current_text, tool_calls=pending_tool_calls,
                    )
                )

                for tc in pending_tool_calls:
                    policy = self.registry.get_policy(tc.name)

                    if policy == ToolPolicy.ASK_FIRST:
                        description = self.registry.get_description(tc.name)
                        approved = await on_tool_request(tc.name, description, tc.arguments)
                        if not approved:
                            result_msg = _create_tool_result_message(
                                tc.id, tc.name, "Отклонено пользователем", is_error=True,
                            )
                            messages.append(result_msg)
                            continue

                    extra: dict[str, object] = {}
                    if context.project_root:
                        extra["project_root"] = context.project_root
                    if context.vector_store:
                        extra["vector_store"] = context.vector_store
                    if on_tool_start:
                        await on_tool_start(tc.name, tc.arguments)
                    result = await self.registry.execute(tc, **extra)
                    messages.append(
                        _create_tool_result_message(
                            tc.id, result.name, result.content, is_error=result.is_error,
                        )
                    )

                continue

            if current_text:
                messages.append(ChatMessage(role="assistant", content=current_text))

            return current_text

        raise AgentIterationLimitError(
            f"Агент превысил лимит итераций ({max_iterations})"
        )
