"""Извлечение текстовых вызовов инструментов из ответа модели.

Модели без нативного tool-calling (встроенная SLM) возвращают вызов
инструмента обычным текстом. Поддерживаются два формата:

1. JSON: ``{"name": "read_file", "arguments": {"path": "a.txt"}}`` —
   как и раньше (в т.ч. в ```json-блоке).
2. Синтаксис вызова функции: ``read_file("a.txt")``,
   ``read_file(path="a.txt")``, ``read_file(path=a.txt)`` —
   в том числе после префикса ``Action:`` / ``## Action``.

Позиционные аргументы сопоставляются с параметрами схемы инструмента
(``required`` идут первыми), поэтому для разбора нужно передать ``tools``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from agents.ollama_client import ToolCall


def schema_param_names(tools: list[Any] | None) -> dict[str, list[str]]:
    """Собирает порядок параметров каждого инструмента из JSON-схем.

    Позиционные аргументы в синтаксисе ``name("value")`` не содержат имени
    параметра, поэтому его нужно взять из схемы. Обязательные параметры
    идут первыми, затем опциональные — в порядке объявления.

    Args:
        tools: Список схем (``ToolSchema`` или dict с ключом ``function``).

    Returns:
        Отображение ``имя_инструмента → список имён параметров``.
    """
    result: dict[str, list[str]] = {}
    for tool in tools or []:
        func = getattr(tool, "function", None)
        if func is None and isinstance(tool, dict):
            func = tool.get("function", tool)
        if not isinstance(func, dict):
            continue
        name = func.get("name")
        if not isinstance(name, str) or not name:
            continue
        params = func.get("parameters", {}) or {}
        props = params.get("properties", {}) or {}
        required = params.get("required", []) or []
        order = [k for k in required if k in props]
        order += [k for k in props if k not in required]
        result[name] = order
    return result


def extract_tool_calls(text: str, tools: list[Any] | None = None) -> list[ToolCall]:
    """Извлекает вызовы инструментов из текстового ответа модели.

    Сначала пробует JSON-формат, затем — синтаксис вызова функции. Второй
    разбор включается только при непустом ``tools``: без списка допустимых
    имён нельзя отличить вызов от простого упоминания функции в тексте.

    Args:
        text: Полный текст ответа модели.
        tools: Схемы доступных инструментов (для позиционных аргументов).

    Returns:
        Список найденных вызовов (возможно пустой).
    """
    params = schema_param_names(tools)
    json_calls = _parse_json_calls(text)
    if params and json_calls:
        json_calls = [c for c in json_calls if c.name in params]
    if json_calls:
        return json_calls

    calls = _parse_function_calls(text, params)
    if calls:
        logger.debug(f"Извлечено текстовых tool_calls (function-call): {len(calls)}")
    else:
        logger.debug("Не удалось извлечь tool_calls из текста")
    return calls


def _parse_json_calls(text: str) -> list[ToolCall]:
    """Разбирает JSON-вызовы вида ``{"name": ..., "arguments": {...}}``."""
    code_block_pattern = r"```(?:json)?\s*\n?(.*?)```"
    match = re.search(code_block_pattern, text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    extracted: list[ToolCall] = []
    stack: list[str] = []
    start: int | None = None

    for i, ch in enumerate(text):
        if ch == "{":
            if not stack:
                start = i
            stack.append(ch)
        elif ch == "}":
            if stack:
                stack.pop()
                if not stack and start is not None:
                    call = _tool_call_from_json(text[start : i + 1])
                    if call is not None:
                        extracted.append(call)
                    start = None

    if not extracted:
        pattern = r'\{"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*({[^}]*})\}'
        for m in re.finditer(pattern, text):
            try:
                name = m.group(1)
                args = json.loads(m.group(2))
                extracted.append(ToolCall(name=name, arguments=args))
            except Exception:
                pass

    return extracted


def _tool_call_from_json(candidate: str) -> ToolCall | None:
    """Парсит фрагмент JSON в ToolCall, при необходимости «чинит» его."""
    for attempt in (candidate, _repair_json(candidate)):
        try:
            parsed = json.loads(attempt)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue

        name: Any = None
        args: Any = None
        if isinstance(parsed.get("name"), str):
            name = parsed["name"]
            args = parsed.get("arguments", {})
        elif isinstance(parsed.get("function"), dict):
            func = parsed["function"]
            name = func.get("name")
            args = func.get("arguments", {})

        if not isinstance(name, str) or not name:
            continue

        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                args = {}
        if not isinstance(args, dict):
            args = {}
        return ToolCall(name=name, arguments=args)
    return None


def _repair_json(text: str) -> str:
    """Грубо правит почти-JSON: без кавычек у ключей, одинарные кавычки."""
    repaired = re.sub(r"(?<={|,)\s*(\w+)\s*:", r' "\1":', text)
    repaired = re.sub(r"(?<!\\)'", '"', repaired)
    return re.sub(r",\s*}", "}", repaired)


def _parse_function_calls(text: str, param_names: dict[str, list[str]]) -> list[ToolCall]:
    """Разбирает вызовы в синтаксисе ``имя(аргументы)``."""
    if not param_names:
        return []

    names = sorted(param_names, key=len, reverse=True)
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\s*\(")

    calls: list[ToolCall] = []
    for match in pattern.finditer(text):
        name = match.group(1)
        args_text, _ = _read_balanced_parens(text, match.end())
        if args_text is None:
            continue
        args = _parse_args(args_text, param_names.get(name, []))
        calls.append(ToolCall(name=name, arguments=args))
    return calls


def _read_balanced_parens(text: str, start: int) -> tuple[str | None, int]:
    """Возвращает содержимое скобок и позицию после закрывающей скобки."""
    depth = 1
    i = start
    quote: str | None = None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    return None, len(text)


def _parse_args(args_text: str, param_order: list[str]) -> dict[str, Any]:
    """Разбирает аргументы вызова в словарь.

    Поддерживает позиционные и именованные аргументы; позиционные
    сопоставляются с ``param_order``.
    """
    stripped = args_text.strip()
    if not stripped:
        return {}

    result: dict[str, Any] = {}
    positional: list[Any] = []
    for part in _split_top_level(args_text):
        part = part.strip()
        if not part:
            continue
        keyword = re.match(r"^(\w+)\s*=\s*(.+)$", part, re.DOTALL)
        if keyword:
            result[keyword.group(1)] = _literal(keyword.group(2))
        else:
            positional.append(_literal(part))

    for i, value in enumerate(positional):
        if i < len(param_order):
            result.setdefault(param_order[i], value)
    return result


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Делит текст по разделителю, не трогая вложенность и кавычки."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    buf: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _literal(token: str) -> Any:
    """Преобразует текстовый аргумент в значение (строка, число, bool).

    Строки в кавычках разворачиваются, экранированные кавычки и обратные
    слэши снимаются. Прочие значения пробуются как JSON; если не вышло —
    остаются строкой (важно для путей Windows вида ``D:\\new``).
    """
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        inner = token[1:-1]
        return inner.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    try:
        return json.loads(token)
    except (json.JSONDecodeError, ValueError):
        return token
