"""Сжатие истории диалога при приближении к лимиту контекста.

История в OlliDesk растёт неограниченно (`ChatPanel._messages`), а окно
локальных моделей маленькое. Здесь — детерминированное прореживание по
приоритету выбрасывания (4.8.E):

  1. `ReasoningPart` в старых assistant-сообщениях → заглушка.
     Самый «дешёвый» к выбрасыванию контент: для Ollama reasoning в промпт
     не идёт (см. `ChatMessage.to_ollama_dict`), для SLM — лишь косвенно
     поддерживает «цепочку мыслей».
  2. Содержимое старых `tool`-сообщений → заглушка.
  3. Старейшие реплики диалога целиком (вместе с их tool-вызовами, чтобы не
     оставлять «осиротевших» tool-сообщений).

Ведущие `system`-сообщения и последняя реплика сохраняются всегда.

После миграции на MessagePart (4.8.A) `ChatMessage.content` — read-only
свойство; замена текста идёт через `with_content()`, замена reasoning —
через пересборку `parts`.
"""

from __future__ import annotations

import json

from loguru import logger

from agents.ollama_client import ChatMessage, ReasoningPart
from core.context_budget import estimate_tokens

# Доля окна контекста, после которой включается сжатие.
COMPACT_THRESHOLD = 0.8

# Сколько последних сообщений диалога не трогаем при опустошении reasoning
# и tool-результатов.
KEEP_RECENT_MESSAGES = 6

# Чем заменяется содержимое отброшенных результатов инструментов.
TOOL_RESULT_STUB = "[результат инструмента опущен при сжатии контекста]"

# Чем заменяется reasoning в старых assistant-сообщениях.
REASONING_STUB = "[рассуждение опущено при сжатии контекста]"

# Нижняя граница бюджета: даже для крошечного окна оставляем место на ответ.
MIN_BUDGET_TOKENS = 256


def messages_tokens(messages: list[ChatMessage]) -> int:
    """Оценивает число токенов в списке сообщений.

    Учитывает не только текст, но и reasoning и аргументы tool-вызовов —
    иначе компактор «не видит» разросшиеся рассуждения (для SLM они уходят
    в промпт через `<thought>…</thought>`, для Ollama — не уходят, но
    занимают память сессии).
    """
    total = 0
    for message in messages:
        total += estimate_tokens(message.content or "")
        total += estimate_tokens(message.reasoning_content or "")
        for call in message.tool_calls or []:
            total += estimate_tokens(call.name)
            arguments = json.dumps(call.arguments, ensure_ascii=False, default=str)
            total += estimate_tokens(arguments)
    return total


def compact_history(
    messages: list[ChatMessage],
    num_ctx: int,
    *,
    threshold: float = COMPACT_THRESHOLD,
    keep_recent: int = KEEP_RECENT_MESSAGES,
) -> list[ChatMessage]:
    """Прореживает историю, если она занимает больше порога окна контекста.

    Порядок шагов: reasoning → tool-результаты → старейшие реплики. После
    каждого шага проверяем, влезли ли в бюджет, и останавливаемся досрочно.
    """
    if not messages or num_ctx <= 0:
        return messages

    budget = max(MIN_BUDGET_TOKENS, int(num_ctx * threshold))
    if messages_tokens(messages) <= budget:
        return messages

    head_len = 0
    while head_len < len(messages) and messages[head_len].role == "system":
        head_len += 1
    head = messages[:head_len]
    dialogue = messages[head_len:]

    logger.info(
        f"Сжатие истории: {messages_tokens(messages)} токенов > {budget} "
        f"({threshold:.0%} от {num_ctx}), реплик {len(dialogue)}"
    )

    # Шаг 1: опустошить reasoning в старых assistant-сообщениях.
    dialogue = _stub_old_reasoning(dialogue, keep_recent)
    if messages_tokens(head) + messages_tokens(dialogue) <= budget:
        logger.info(f"Сжатие истории: опустошён reasoning ({len(dialogue)} реплик)")
        return head + dialogue

    # Шаг 2: опустошить содержимое старых tool-результатов.
    dialogue = _stub_old_tool_results(dialogue, keep_recent)
    if messages_tokens(head) + messages_tokens(dialogue) <= budget:
        logger.info(f"Сжатие истории: опустошены старые tool-результаты ({len(dialogue)} реплик)")
        return head + dialogue

    # Шаг 3: выбросить старейшие реплики целиком.
    dialogue = _drop_oldest_turns(dialogue, budget - messages_tokens(head))
    logger.info(f"Сжатие истории: после отбрасывания реплик осталось {len(dialogue)}")
    return head + dialogue


def _stub_old_reasoning(dialogue: list[ChatMessage], keep_recent: int) -> list[ChatMessage]:
    """Заменяет ReasoningPart в старых assistant-сообщениях на заглушку.

    Reasoning в истории — временный контекст. Для Ollama он вообще не идёт
    в промпт (`to_ollama_dict` отправляет только `content`), для SLM даёт
    лишь косвенную поддержку chain-of-thought. Поэтому при нехватке бюджета
    выбрасывается ПЕРВЫМ — раньше tool-результатов.
    """
    protected_from = max(0, len(dialogue) - keep_recent)
    result: list[ChatMessage] = []
    for index, message in enumerate(dialogue):
        if (
            index < protected_from
            and message.role == "assistant"
            and any(isinstance(p, ReasoningPart) for p in message.parts)
        ):
            new_parts = [
                ReasoningPart(text=REASONING_STUB) if isinstance(p, ReasoningPart) else p
                for p in message.parts
            ]
            result.append(
                ChatMessage(
                    id=message.id,
                    role=message.role,
                    parts=new_parts,
                    images=message.images,
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                )
            )
        else:
            result.append(message)
    return result


def _stub_old_tool_results(dialogue: list[ChatMessage], keep_recent: int) -> list[ChatMessage]:
    """Заменяет содержимое старых `tool`-сообщений на короткую пометку.

    ``ChatMessage.content`` — read-only (property), поэтому замена идёт через
    ``with_content()``: это создаёт копию с новым TextPart, сохраняя
    ``tool_call_id``/``name`` и прочие части.
    """
    protected_from = max(0, len(dialogue) - keep_recent)
    result: list[ChatMessage] = []
    for index, message in enumerate(dialogue):
        if index < protected_from and message.role == "tool" and message.content != TOOL_RESULT_STUB:
            result.append(message.with_content(TOOL_RESULT_STUB))
        else:
            result.append(message)
    return result


def _split_turns(dialogue: list[ChatMessage]) -> list[list[ChatMessage]]:
    """Группирует сообщения в реплики: новая реплика начинается с `user`."""
    turns: list[list[ChatMessage]] = []
    for message in dialogue:
        if message.role == "user" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return turns


def _drop_oldest_turns(dialogue: list[ChatMessage], budget: int) -> list[ChatMessage]:
    """Выбрасывает старейшие реплики, пока список не уложится в бюджет."""
    turns = _split_turns(dialogue)
    while len(turns) > 1:
        flattened = [message for turn in turns for message in turn]
        if messages_tokens(flattened) <= budget:
            break
        turns.pop(0)
    return [message for turn in turns for message in turn]
