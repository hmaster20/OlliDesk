"""Схема событий OlliDesk (2.5).

`EventBus` передаёт `dict`. Здесь — контракты (TypedDict) для type hints
и документации.

**Почему TypedDict, а не Pydantic:** это горячий путь (`log`-события,
`tool.*`, `llm.*` в цикле по токенам). Pydantic-валидация на каждый emit
добавила бы 10–100 мкс на событие — в логах это незаметно, в стриминге
токенов — неприемлемо. Валидация при необходимости делается в тестах
(см. `tests/core/test_event_bus.py`) или в конкретном подписчике.

Правило именования: `<domain>.<action>` (lowercase, dot-separated).
Domain — префикс до первой точки (`tool`, `llm`, `rag`, …).
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import NotRequired, TypedDict

class EventKind(StrEnum):
    """Все виды событий OlliDesk.

    Подписчики используют эти константы; добавление нового вида
    не ломает существующих (они фильтруют по `kind`).
    """

    # --- RAG ---
    RAG_SEARCH_STARTED = "rag.search.started"
    RAG_SEARCH_FOUND = "rag.search.found"
    RAG_SEARCH_EMPTY = "rag.search.empty"

    # --- LLM ---
    LLM_REQUEST_SENT = "llm.request.sent"
    LLM_FIRST_TOKEN = "llm.first_token"
    LLM_GENERATION_DONE = "llm.generation.done"

    # --- Tools ---
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    TOOL_ERROR = "tool.error"

    # --- Research phases ---
    PHASE_CHANGED = "phase.changed"

    # --- Indexing ---
    INDEX_STARTED = "index.started"
    INDEX_PROGRESS = "index.progress"
    INDEX_FINISHED = "index.finished"

    # --- SLM loading ---
    SLM_LOADING = "slm.loading"
    SLM_LOADED = "slm.loaded"
    SLM_FAILED = "slm.failed"

    # --- Orchestrator ---
    ORCHESTRATOR_EVENT = "orchestrator.event"

    # --- Log mirror (loguru sink) ---
    LOG = "log"


# ---------------------------------------------------------------------------
# Payload-контракты (TypedDict — type hints, не runtime-валидация)
# ---------------------------------------------------------------------------


class RagSearchStartedPayload(TypedDict):
    query: str
    n_results: int


class RagSearchFoundPayload(TypedDict):
    query: str
    n_results: int
    duration_ms: float


class RagSearchEmptyPayload(TypedDict):
    query: str
    duration_ms: float


class LlmRequestSentPayload(TypedDict):
    model: str
    backend: str  # "ollama" | "slm" | "rule"
    n_messages: int


class LlmFirstTokenPayload(TypedDict):
    model: str
    ttft_ms: float


class LlmGenerationDonePayload(TypedDict):
    model: str
    ttft_ms: NotRequired[float]
    tokens_per_sec: float
    tokens: int


class ToolStartedPayload(TypedDict):
    name: str
    args_hash: str


class ToolFinishedPayload(TypedDict):
    name: str
    duration_ms: float
    is_error: bool
    error_category: NotRequired[str]


class ToolErrorPayload(TypedDict):
    name: str
    error_category: str
    message: str


class PhaseChangedPayload(TypedDict):
    phase: str  # "recon" | "deep_dive" | "self_check" | "synthesis"
    iteration: int
    max_iterations: int


class IndexStartedPayload(TypedDict):
    project_root: str
    total_files: int


class IndexProgressPayload(TypedDict):
    current: int
    total: int


class IndexFinishedPayload(TypedDict):
    indexed: int
    deleted: int
    duration_ms: float


class SlmLoadingPayload(TypedDict):
    model_id: str
    path: str


class SlmLoadedPayload(TypedDict):
    model_id: str
    load_ms: float


class SlmFailedPayload(TypedDict):
    model_id: str
    error: str


class OrchestratorEventPayload(TypedDict):
    kind: str  # "classify" | "plan" | "coverage" | "synthesize" | "regen" | …
    data: dict


class LogPayload(TypedDict):
    level: str  # "INFO" | "WARNING" | "ERROR" | "CRITICAL"
    message: str
    source: str  # "module:function"
    timestamp: str  # "HH:MM:SS"

def args_hash(args: dict) -> str:
    """Стабильный 16-символьный хеш аргументов tool-call'а.

    Используется в ``tool.started``/``tool.finished`` (Stage B 2.5) и
    в ``core.audit`` (0.1.1, там свой — но результат эквивалентен по
    смыслу). ``sort_keys=True`` — аргументы одного вызова дают одинаковый
    хеш независимо от порядка. ``default=str`` — fallback на несериализуемые
    значения (Path и т.п.), чтобы функция никогда не бросала.

    Args:
        args: Аргументы tool-call'а (dict из `ToolCall.arguments`).

    Returns:
        16 hex-символов (sha256[0:16]).
    """
    try:
        payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(args)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]

__all__ = [
    "EventKind",
    "IndexFinishedPayload",
    "IndexProgressPayload",
    "IndexStartedPayload",
    "LlmFirstTokenPayload",
    "LlmGenerationDonePayload",
    "LlmRequestSentPayload",
    "LogPayload",
    "OrchestratorEventPayload",
    "PhaseChangedPayload",
    "RagSearchEmptyPayload",
    "RagSearchFoundPayload",
    "RagSearchStartedPayload",
    "SlmFailedPayload",
    "SlmLoadedPayload",
    "SlmLoadingPayload",
    "ToolErrorPayload",
    "ToolFinishedPayload",
    "ToolStartedPayload",
    "args_hash",
]