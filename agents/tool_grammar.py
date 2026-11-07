"""Парсинг и валидация tool-call'ов локальных SLM.

Локальные 3B/7B не имеют нативного function-calling. Задача модуля:
  • parse_tool_call: извлечь ToolCall из текста ответа модели;
  • looks_like_ascii_junk: детектор «каракулей» вместо схемы (чинит D9);
  • should_fallback: нужен ли deterministic сбор данных кодом (чинит D4).
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from agents.ollama_client import ToolCall
from agents.tool_call_parser import extract_tool_calls

_ASCII_JUNK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[|+\-]{5,}"),
    re.compile(r"(?:\|\s*){3,}\|"),
    re.compile(r"(?:[-+]\s*){5,}"),
    re.compile(r"^\s*[|+\-\s]{12,}$", re.MULTILINE),
    re.compile(r"^\s*\|[\s\-|/\\]{6,}\|\s*$", re.MULTILINE),
)

_REACT_LEAK_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:action|observation|thought)\s*[:：]",
)

_MIN_LEN_FOR_JUNK_CHECK = 20
_JUNK_HITS_THRESHOLD = 3


def looks_like_ascii_junk(text: str) -> bool:
    """True, если текст — характерный ASCII-мусор вместо схемы."""
    if not text or len(text) < _MIN_LEN_FOR_JUNK_CHECK:
        return False
    hits = 0
    for pattern in _ASCII_JUNK_PATTERNS:
        hits += len(pattern.findall(text))
        if hits >= _JUNK_HITS_THRESHOLD:
            return True
    return False


def parse_tool_call(
    text: str,
    tools: list[Any] | None = None,
) -> ToolCall | None:
    """Извлекает первый tool-call из текста ответа модели."""
    if not text:
        return None
    calls = extract_tool_calls(text, tools)
    if calls:
        logger.debug(f"tool_grammar: извлечено tool_calls={len(calls)}")
    return calls[0] if calls else None


def should_fallback(
    text: str,
    tool_calls: list[ToolCall] | None = None,
) -> bool:
    """True — оркестратор должен собрать данные кодом (без LLM-цикла)."""
    if tool_calls:
        return False
    if not text:
        return True
    if looks_like_ascii_junk(text):
        return True
    return bool(_REACT_LEAK_RE.search(text))
