"""Цикл рассуждений и действий (ReAct) агента."""

import json
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

Приоритет источников (сверху вниз):
  1. КОД ПРОЕКТА — главный источник истины. Изучай его инструментами:
     • list_directory  — обзор структуры (папки, роли, файлы);
     • read_file       — точное содержимое нужного файла;
     • search_codebase — смысловой поиск по коду.
  2. СВОИ ЗНАНИЯ — применяй их, чтобы анализировать код, находить закономерности,
     ошибки и расхождения с документацией.
  3. web_search — ТОЛЬКО для внешних/публичных сведений (версии ПО, новости,
     публичная документация), и только если ты НЕ уверен. НЕ ищи в интернете
     внутренние детали проекта (имена ролей, внутренние файлы, конфиги) — их там нет.
     Перед web_search сначала прочитай соответствующий код.

Когда какой инструмент:
  • содержимое/структура проекта — read_file, list_directory, search_codebase
  • текущие дата и время         — get_current_datetime
  • погода в городе              — get_weather
  • курсы валют                  — get_exchange_rate
  • внешние/публичные сведения   — web_search (только при сомнении)

НЕ отвечай «я не могу получить актуальную информацию» — у тебя есть инструменты.
НЕ советуй пользователю «попробуйте web_search» — вызови инструмент САМ.

Порядок работы (ReAct):
  1. Thought — что нужно узнать и В КАКИХ ФАЙЛАХ это лежит.
  2. Action  — вызов инструмента (нативный tool_call или JSON в тексте).
  3. Observation — результат вернётся тебе автоматически.
  4. Next Thought — что ещё прочитать? Если данных достаточно — переходи к ответу.
  5. Final Answer — структурированный ответ (см. формат ниже).

Формат ответа (обязательно):
  • Пиши на русском, СТРУКТУРИРОВАННО, в Markdown — НЕ сплошным текстом.
  • Заголовки разделов — через ## и ###.
  • Перечисления — нумерованные (1., 2.) или маркированные (- ) списки.
  • Код, конфиги и правки — в блоках ```язык (например ```yaml).
  • Указывай относительные пути к разобранным файлам.
  • Заканчивай кратким выводом и конкретными рекомендациями.

ВАЖНО про повторы:
  • Получил результат инструмента — ИСПОЛЬЗУЙ его и продвигайся к ответу.
  • НЕ вызывай один и тот же инструмент дважды подряд с одинаковыми аргументами:
    результат не изменится, а ответ задержится.
  • Если данных не хватает — читай ДРУГОЙ файл или вызывай ДРУГОЙ инструмент.
  • Если результат не помог — скажи об этом прямо, а не повторяй тот же вызов.
""".strip()


_MODE_HINTS = {
    AgentMode.CHAT: (
        "Текущий режим: ЧАТ.\n"
        "Общайся с пользователем, при необходимости используй read-only "
        "инструменты (дата, погода, курсы, чтение файлов, web_search). "
        "Запись файлов и git-операции недоступны."
    ),
    AgentMode.PLAN: (
        "Текущий режим: ПЛАН.\n"
        "Сначала изучи структуру и код проекта read-only инструментами, найди "
        "закономерности и отличия, и только потом составь подробный план. "
        "Запись файлов и git-операции недоступны."
    ),
    AgentMode.AGENT: (
        "Текущий режим: АГЕНТ.\n"
        "Сначала изучи структуру и код проекта инструментами, опирайся на свои "
        "знания для анализа, и только при сомнении обращайся к web_search. "
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

    @staticmethod
    def _tool_signature(calls: list[ToolCall]) -> tuple[tuple[str, str], ...]:
        """Строит сигнатуру набора вызовов для обнаружения зацикливания."""
        sig: list[tuple[str, str]] = []
        for tc in calls:
            try:
                args = json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False)
            except (TypeError, ValueError):
                args = str(tc.arguments)
            sig.append((tc.name, args))
        return tuple(sorted(sig))

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

        prev_signature: tuple[tuple[str, str], ...] | None = None

        for iteration in range(max_iterations):
            logger.debug(
                f"ReAct итерация {iteration + 1}/{max_iterations}, "
                f"режим={context.mode.value}, tools={len(tools_to_send or [])}"
            )

            current_text = ""
            pending_tool_calls: list[ToolCall] = []

            if on_status:
                if iteration == 0:
                    await on_status("🤔 Анализирую запрос…")
                else:
                    await on_status(f"🧠 Размышляю над результатами (шаг {iteration + 1}/{max_iterations})…")

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

            # --- Защита от повторов ---
            if pending_tool_calls and tools_to_send:
                if on_status:
                    names = ", ".join(dict.fromkeys(tc.name for tc in pending_tool_calls))
                    await on_status(f"🔧 Подготовка вызовов: {names}")
                signature = self._tool_signature(pending_tool_calls)
                if signature == prev_signature:
                    logger.warning("Повтор идентичного набора tool_calls — прерываю цикл")
                    if on_status:
                        await on_status("⏹ Обнаружено повторение вызовов — завершаю")
                    if current_text:
                        messages.append(ChatMessage(role="assistant", content=current_text))
                        return current_text
                    return "Агент остановлен: повторяющиеся вызовы инструментов без прогресса."
                prev_signature = signature

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
