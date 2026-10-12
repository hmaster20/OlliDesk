"""Цикл рассуждений и действий (ReAct) агента."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from agents.base import LLMBackend
from agents.history_compactor import compact_history
from agents.ollama_client import ChatMessage, ToolCall
from agents.tool_call_parser import extract_tool_calls
from agents.tool_registry import ToolRegistry
from core.config import AgentMode, ToolPolicy
from core.context_budget import MIN_SECTION_CHARS, system_prompt_char_budget, truncate_to_chars
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
    project_name: str = ""
    project_map: str = ""
    vector_store: object = None
    extra_rules: str = ""
    max_tokens: int = 4096
    num_ctx: int = 0
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
     • search_codebase — смысловой поиск по коду (сузь через language=, path= и/или даты modified_after=/modified_before=);
     В конце промпта есть КАРТА ПРОЕКТА: сверься с ней, чтобы понять, какие
     каталоги и файлы существуют, и читай нужные по относительному пути.
     Если вопрос про каталог — изучи ИМЕННО его файлы, не ограничивайся
     фрагментами, которые уже попали в контекст.
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


_LANGUAGE_DIRECTIVE = (
    "ЯЗЫК ОТВЕТА: пиши ТОЛЬКО на русском языке. Не вставляй слова, фразы или "
    "иероглифы на других языках — даже для технических терминов, если у них "
    "есть общепринятый русский аналог. Отвечай на другом языке только если "
    "пользователь явно об этом попросил."
)


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


_SECTION_SEP = "\n\n"


def _project_block(context: AgentContext) -> str:
    """Собирает блок о проекте: имя и корень, от которого считаются пути."""
    lines: list[str] = []
    if context.project_name:
        lines.append(f"Проект: {context.project_name}")
    if context.project_root:
        lines.append(f"Корень проекта: {context.project_root}")
        lines.append("Все пути к файлам указывай относительными от корня проекта.")
    return "\n".join(lines)


def _assemble_prompt(pre: list[str], variable: list[tuple[str, str]]) -> str:
    """Склеивает промпт: фиксированные блоки, переменные секции, языковая директива.

    Языковая директива всегда последняя: модели сильнее следуют поздним инструкциям.
    """
    return _SECTION_SEP.join(pre + [text for _, text in variable] + [_LANGUAGE_DIRECTIVE])


def _fit_sections(
    pre: list[str],
    variable: list[tuple[str, str]],
    num_ctx: int,
) -> list[tuple[str, str]]:
    """Урезает переменные секции, если промпт не влезает в бюджет окна модели.

    Фиксированные блоки (роль, инструкции по tools, подсказка режима, блок
    проекта) не трогаются. Переменные секции урезаются в порядке приоритета —
    от наименее важной: RAG → карта проекта → правила. Секцию, которую нельзя
    урезать до осмысленного размера, выбрасывают целиком. При неизвестном окне
    (`num_ctx <= 0`) промпт отдаётся без изменений.
    """
    if num_ctx <= 0 or not variable:
        return variable
    budget = system_prompt_char_budget(num_ctx)
    if len(_assemble_prompt(pre, variable)) <= budget:
        return variable

    remaining = list(variable)
    for key in ("rag", "map", "rules"):
        if len(_assemble_prompt(pre, remaining)) <= budget:
            break
        others = [item for item in remaining if item[0] != key]
        # Каждая секция добавляет к склейке один разделитель (_SECTION_SEP).
        allowance = budget - len(_assemble_prompt(pre, others)) - len(_SECTION_SEP)
        if allowance <= MIN_SECTION_CHARS:
            remaining = others
            continue
        remaining = [
            (name, truncate_to_chars(value, allowance) if name == key else value) for name, value in remaining
        ]
        remaining = [(name, value) for name, value in remaining if value]
    return remaining


def _build_system_prompt(context: AgentContext) -> str:
    """Собирает system-промпт из контекста, роли и инструкций по tools.

    Фиксированные блоки (роль, instructions, подсказка режима, проект, языковая
    директива) и переменные секции (правила, карта проекта, RAG) укладываются в
    бюджет ``system_prompt_char_budget(num_ctx)``; при переполнении переменные
    секции урезаются в порядке приоритета (см. ``_fit_sections``).
    """
    base_prompt = context.system_prompt or "Ты — полезный AI-ассистент."

    pre = [base_prompt, _TOOL_USE_INSTRUCTION]
    pre.append(_MODE_HINTS.get(context.mode, _MODE_HINTS[AgentMode.CHAT]))
    project_block = _project_block(context)
    if project_block:
        pre.append(project_block)

    variable: list[tuple[str, str]] = []
    if context.extra_rules:
        variable.append(("rules", f"Дополнительные правила:\n{context.extra_rules}"))
    if context.project_map:
        variable.append(("map", context.project_map))
    if context.rag_context:
        variable.append(("rag", f"Контекст из кодовой базы:\n{context.rag_context}"))

    variable = _fit_sections(pre, variable, context.num_ctx)

    return _assemble_prompt(pre, variable)


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

    def _compact_history(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Прореживает промпт, если он не помещается в окно модели.

        Размер окна берётся у активного бэкенда (`num_ctx`); если он неизвестен,
        сжатие пропускается — финальную подгонку сделает бэкенд.
        """
        num_ctx = getattr(self.client, "num_ctx", None)
        if not isinstance(num_ctx, int) or num_ctx <= 0:
            return messages
        return compact_history(messages, num_ctx)

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

    def _extract_tool_calls_from_text(self, text: str, tools: list | None = None) -> list[ToolCall]:
        """Извлекает вызовы инструментов из текстового ответа модели.

        Поддерживает JSON (``{"name": ..., "arguments": {...}}``) и синтаксис
        вызова функции (``name("arg")`` / ``name(key=value)``). Детали — в
        :mod:`agents.tool_call_parser`.
        """
        return extract_tool_calls(text, tools)

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
        messages = self._compact_history(messages)

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
                extracted_calls = self._extract_tool_calls_from_text(current_text, tools_to_send)
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
