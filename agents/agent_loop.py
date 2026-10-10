"""Цикл рассуждений и действий (ReAct) агента."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from agents.base import LLMBackend
from agents.ollama_client import ChatMessage, ToolCall
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
    search_cache: object = None
    attached_files: object = None


# --- System prompt ---------------------------------------------------------

_TOOL_USE_INSTRUCTION = """
ВАЖНО: у тебя есть ИНСТРУМЕНТЫ (tools), доступные через API.
Когда пользователь спрашивает про:
  • текущую дату/время — вызови get_current_datetime
  • погоду в городе     — вызови get_weather
  • курсы валют         — вызови get_exchange_rate
  • свежие новости, ставки ЦБ, версии ПО, релизы, цены, законы,
    любые данные, которые могли измениться после твоего обучения
                        — вызови web_search
  • содержимое файлов проекта — read_file
  • поиск по кодовой базе     — search_codebase
  • список файлов в папке     — list_directory

НЕ отвечай «я не могу получить актуальную информацию» — у тебя есть
инструменты для этого. НЕ советуй пользователю «попробуйте web_search» —
вызови инструмент САМ.

Порядок работы (ReAct):
  1. Thought — что нужно узнать, чтобы ответить?
  2. Action  — вызов инструмента (нативный tool_call или JSON в тексте).
  3. Observation — результат вернётся тебе автоматически.
  4. Next Thought — достаточно ли данных? Если нет, повтори цикл.
  5. Final Answer — когда данных достаточно, сформируй ответ.

ВАЖНО про повторы:
  • Получил результат инструмента — ИСПОЛЬЗУЙ его и сразу дай ответ пользователю.
  • НЕ вызывай один и тот же инструмент дважды подряд с одинаковыми аргументами:
    результат не изменится, а ответ задержится.
  • Если данных не хватает — вызывай ДРУГОЙ инструмент или с другими аргументами
    (другой запрос, другой файл, другая валюта).
  • Если результат инструмента не помог — скажи об этом пользователю прямо,
    а не повторяй тот же вызов.
""".strip()


_MODE_HINTS = {
    AgentMode.CHAT: (
        "Текущий режим: ЧАТ.\n"
        "Общайся с пользователем, при необходимости используй read-only "
        "инструменты (дата, погода, курсы, web_search, чтение файлов). "
        "Запись файлов и git-операции недоступны."
    ),
    AgentMode.PLAN: (
        "Текущий режим: ПЛАН.\n"
        "Исследуй проект read-only инструментами, составь план действий. "
        "Запись файлов и git-операции недоступны."
    ),
    AgentMode.AGENT: (
        "Текущий режим: АГЕНТ.\n"
        "У тебя полный доступ: чтение, запись файлов, git-снапшоты, поиск. "
        "Выполняй задачу автономно и пошагово, используя инструменты."
    ),
}


def _build_system_prompt(context: AgentContext) -> str:
    """Собирает system-промпт из контекста, роли и инструкций по tools."""
    base_prompt = context.system_prompt or "Ты — полезный AI-ассистент."

    parts = [base_prompt, _TOOL_USE_INSTRUCTION]

    mode_hint = _MODE_HINTS.get(context.mode, _MODE_HINTS[AgentMode.CHAT])
    parts.append(mode_hint)

    if context.rag_context:
        parts.append(f"Контекст из кодовой базы:\n{context.rag_context}")
    if context.extra_rules:
        parts.append(f"Дополнительные правила:\n{context.extra_rules}")
    if context.project_root:
        parts.append(
            f"Корень проекта: {context.project_root}\n"
            "Все пути к файлам указывай относительными от корня проекта."
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
    """Цикл ReAct агента.

    Работает для всех режимов (CHAT, PLAN, AGENT). Ограничения на доступные
    инструменты накладываются через ToolRegistry.get_schemas(mode).
    """

    def __init__(self, client: LLMBackend, registry: ToolRegistry, model: str):
        """
        Args:
            client: Клиент LLM (OllamaClient, LocalLLMClient, ModelRouter)
            registry: Реестр инструментов
            model: Имя модели
        """
        self.client = client
        self.registry = registry
        self.model = model

    def _extract_tool_calls_from_text(self, text: str) -> list[ToolCall]:
        """Извлекает вызовы инструментов из текстового ответа модели."""
        import json as _json
        import re

        code_block_pattern = r"```(?:json)?\s*\n?(.*?)```"
        match = re.search(code_block_pattern, text, re.DOTALL)
        if match:
            text = match.group(1).strip()

        extracted: list[ToolCall] = []
        stack: list[str] = []
        start = None

        for i, ch in enumerate(text):
            if ch == "{":
                if not stack:
                    start = i
                stack.append(ch)
            elif ch == "}":
                if stack:
                    stack.pop()
                    if not stack and start is not None:
                        candidate = text[start : i + 1]
                        try:
                            parsed = _json.loads(candidate)
                            if "name" in parsed and "arguments" in parsed:
                                extracted.append(
                                    ToolCall(
                                        name=parsed["name"],
                                        arguments=parsed["arguments"],
                                    )
                                )
                            elif "function" in parsed and "name" in parsed["function"]:
                                func = parsed["function"]
                                extracted.append(
                                    ToolCall(
                                        name=func["name"],
                                        arguments=func.get("arguments", {}),
                                    )
                                )
                        except _json.JSONDecodeError:
                            repaired = candidate
                            repaired = re.sub(r"(?<={|,)\s*(\w+)\s*:", r' "\1":', repaired)
                            repaired = re.sub(r"(?<!\\)'", '"', repaired)
                            repaired = re.sub(r",\s*}", "}", repaired)
                            try:
                                parsed = _json.loads(repaired)
                                if "name" in parsed and "arguments" in parsed:
                                    extracted.append(
                                        ToolCall(
                                            name=parsed["name"],
                                            arguments=parsed["arguments"],
                                        )
                                    )
                                elif "function" in parsed and "name" in parsed["function"]:
                                    func = parsed["function"]
                                    extracted.append(
                                        ToolCall(
                                            name=func["name"],
                                            arguments=func.get("arguments", {}),
                                        )
                                    )
                            except _json.JSONDecodeError:
                                pass
                        start = None

        if not extracted:
            pattern = r'\{"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*({[^}]*})\}'
            for m in re.finditer(pattern, text):
                try:
                    name = m.group(1)
                    args = _json.loads(m.group(2))
                    extracted.append(ToolCall(name=name, arguments=args))
                except Exception:
                    pass

        if extracted:
            logger.debug(f"Извлечено tool_calls из текста: {len(extracted)}")
        else:
            logger.debug("Не удалось извлечь tool_calls из текста")

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
        on_status: Callable[[str], Awaitable[None]] | None = None,
        on_tool_result: Callable[[str, str, bool], Awaitable[None]] | None = None,
    ) -> str:
        messages = [ChatMessage(role="system", content=_build_system_prompt(context))]
        messages.extend(messages_history)

        tools_to_send: list | None = self.registry.get_schemas(context.mode)
        if tools_to_send is not None and len(tools_to_send) == 0:
            tools_to_send = None

        for iteration in range(max_iterations):
            logger.debug(
                f"ReAct итерация {iteration + 1}/{max_iterations}, "
                f"режим={context.mode.value}, tools={len(tools_to_send or [])}"
            )

            current_text = ""
            pending_tool_calls: list[ToolCall] = []

            if on_status:
                await on_status("🤔 Модель думает…" if iteration == 0 else "🤔 Планирует следующий шаг…")

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
                    logger.info("Запрос отменён пользователем")
                    return current_text or "Отменено"

            except ToolsNotSupportedError:
                logger.warning(f"Модель {self.model} не поддерживает нативные tools, повтор без tools")
                tools_to_send = None
                if on_tools_disabled:
                    await on_tools_disabled()
                if current_text:
                    await on_token("\n\n")
                continue

            # --- Fallback: текстовые tool_calls ---
            if not pending_tool_calls and current_text and tools_to_send:
                extracted_calls = self._extract_tool_calls_from_text(current_text)
                if extracted_calls:
                    pending_tool_calls = extracted_calls
                    current_text = ""
                    logger.debug(f"Извлечено {len(pending_tool_calls)} текстовых tool_calls")

            # --- Исполняем ---
            if pending_tool_calls and tools_to_send:
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=current_text,
                        tool_calls=pending_tool_calls,
                    )
                )

                for tc in pending_tool_calls:
                    policy = self.registry.get_policy(tc.name)

                    if policy == ToolPolicy.ASK_FIRST:
                        description = self.registry.get_description(tc.name)
                        approved = await on_tool_request(tc.name, description, tc.arguments)
                        if not approved:
                            messages.append(
                                _create_tool_result_message(
                                    tc.id,
                                    tc.name,
                                    "Отклонено пользователем",
                                    is_error=True,
                                )
                            )
                            continue

                    extra: dict[str, object] = {}
                    if context.project_root:
                        extra["project_root"] = context.project_root
                    if context.vector_store:
                        extra["vector_store"] = context.vector_store
                    if context.search_cache:
                        extra["search_cache"] = context.search_cache
                    if context.attached_files:
                        extra["attached_files"] = context.attached_files
                    if on_tool_start:
                        await on_tool_start(tc.name, tc.arguments)

                    result = await self.registry.execute(tc, **extra)
                    if on_tool_result:
                        await on_tool_result(result.name, result.content, result.is_error)
                    messages.append(
                        _create_tool_result_message(
                            tc.id,
                            result.name,
                            result.content,
                            is_error=result.is_error,
                        )
                    )

                continue

            # Нет tools — финальный ответ
            if current_text:
                messages.append(ChatMessage(role="assistant", content=current_text))

            return current_text

        raise AgentIterationLimitError(f"Агент превысил лимит итераций ({max_iterations})")
