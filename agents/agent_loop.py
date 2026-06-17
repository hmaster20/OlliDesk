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
    """Собирает system-промпт из контекста и роли."""
    mode_name = {AgentMode.PLAN: "План", AgentMode.AGENT: "Агент"}.get(context.mode, "Агент")

    # Базовый промт из роли (или дефолтный)
    base_prompt = context.system_prompt or "Ты — полезный AI-ассистент."

    parts = [base_prompt, f"\nТекущий режим работы: {mode_name}."]

    # if context.mode == AgentMode.PLAN:
    #     parts.append(
    #         "В режиме Плана тебе доступны только read-only инструменты (чтение файлов, поиск). "
    #         "Не выводи JSON-план текстом — сразу вызывай нужные инструменты через API."
    #     )
    # elif context.mode == AgentMode.AGENT:
    #     parts.append(
    #         "В режиме Агента у тебя есть доступ ко всем инструментам, включая запись файлов и git. "
    #         "Используй их для автономного выполнения задачи."
    #     )

    if context.rag_context:
        parts.append(f"\nКонтекст из кодовой базы:\n{context.rag_context}")
    if context.extra_rules:
        parts.append(f"\nДополнительные правила:\n{context.extra_rules}")
    if context.project_root:
        parts.append(
            f"\nКорень проекта: {context.project_root}\n"
            "Все пути к файлам должны быть относительными от корня проекта."
        )

    return "\n".join(parts)

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

    def _extract_tool_calls_from_text(self, text: str) -> list[ToolCall]:
        """Извлекает все возможные вызовы инструментов из текстового ответа модели."""
        import json as _json
        import re

        # Ищем все JSON-объекты в тексте (включая вложенные)
        json_objects = []
        # Простейший поиск: находим блоки, начинающиеся с { и заканчивающиеся }
        # Но нужно учитывать вложенность, поэтому используем стек
        stack = []
        start = None
        for i, ch in enumerate(text):
            if ch == '{':
                if not stack:
                    start = i
                stack.append(ch)
            elif ch == '}':
                if stack:
                    stack.pop()
                    if not stack and start is not None:
                        json_objects.append(text[start:i+1])
                        start = None

        extracted = []
        for raw in json_objects:
            # Пробуем "починить" JSON
            repaired = raw
            # 1. Добавляем кавычки к ключам, если их нет
            repaired = re.sub(r'(?<={|,)\s*(\w+)\s*:', r' "\1":', repaired)
            # 2. Заменяем одинарные кавычки на двойные (если они не экранированы)
            repaired = re.sub(r"(?<!\\)'", '"', repaired)
            # 3. Убираем trailing commas (иногда модель ставит запятую перед } или ])
            repaired = re.sub(r',\s*}', '}', repaired)
            repaired = re.sub(r',\s*]', ']', repaired)
            # 4. Убираем лишние пробелы внутри чисел (например, "max_results": 5)
            #    но это не критично

            try:
                parsed = _json.loads(repaired)
                # Проверяем, есть ли поля name и arguments
                if "name" in parsed and "arguments" in parsed:
                    # Также может быть "type": "function" и "function": {...}
                    if parsed.get("type") == "function" and "function" in parsed:
                        func = parsed["function"]
                        name = func.get("name")
                        args = func.get("arguments", {})
                    else:
                        name = parsed["name"]
                        args = parsed.get("arguments", {})
                    if name:
                        tc = ToolCall(name=name, arguments=args)
                        extracted.append(tc)
            except _json.JSONDecodeError:
                # Если не удалось, пробуем извлечь name и arguments с помощью regex
                name_match = re.search(r'"name"\s*:\s*"([^"]+)"', repaired)
                args_match = re.search(r'"arguments"\s*:\s*({[^}]*})', repaired)
                if name_match and args_match:
                    try:
                        args = _json.loads(args_match.group(1))
                        extracted.append(ToolCall(name=name_match.group(1), arguments=args))
                    except _json.JSONDecodeError:
                        pass
                # Также пробуем вариант без кавычек у ключей
                name_match2 = re.search(r'name\s*:\s*"([^"]+)"', repaired)
                args_match2 = re.search(r'arguments\s*:\s*({[^}]*})', repaired)
                if name_match2 and args_match2:
                    try:
                        args = _json.loads(args_match2.group(1))
                        extracted.append(ToolCall(name=name_match2.group(1), arguments=args))
                    except _json.JSONDecodeError:
                        pass
        return extracted


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

            # --- УЛУЧШЕННЫЙ FALLBACK ДЛЯ ТЕКСТОВЫХ JSON-ВЫЗОВОВ ---
            if not pending_tool_calls and current_text and tools_to_send:
                extracted_calls = self._extract_tool_calls_from_text(current_text)
                if extracted_calls:
                    pending_tool_calls = extracted_calls
                    # Очищаем текст, чтобы не дублировать вызов
                    current_text = ""
                    logger.debug(f"Извлечено {len(pending_tool_calls)} текстовых tool_calls")
                else:
                    # Если JSON невалидный, но мы не смогли извлечь, просто оставляем текст
                    logger.debug("Не удалось извлечь вызовы из текста, продолжаем")

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
