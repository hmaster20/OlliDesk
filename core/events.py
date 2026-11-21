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
    """Все виды событий OlliDesk (2.5).

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

    # --- 2.10 / 2.5: бюджет и завершение агента ---
    BUDGET_WARNING = "budget.warning"
    BUDGET_EXCEEDED = "budget.exceeded"
    AGENT_FINISHED = "agent.finished"

    # --- 4.3: эпизодическая память ---
    MEMORY_RECALLED = "memory.recalled"
    MEMORY_REMEMBERED = "memory.remembered"

    # --- 2.7: policy ---
    POLICY_OVERRIDE = "policy.override"

    # --- 7: MCP ---
    MCP_CONNECTED = "mcp.connected"
    MCP_DISCONNECTED = "mcp.disconnected"
    MCP_TOOL_CALLED = "mcp.tool_called"
    MCP_ERROR = "mcp.error"

    # --- 2.11: browser ---
    BROWSER_ACTION = "browser.action"

    # --- 2.13: цепочка поиска ---
    SEARCH_PROVIDER_USED = "search.provider.used"
    SEARCH_CHAIN_EXHAUSTED = "search.chain.exhausted"

    # --- 2.12: durable inbox ---
    INBOX_ENQUEUED = "inbox.enqueued"
    INBOX_DELIVERED = "inbox.delivered"
    INBOX_FAILED = "inbox.failed"

    # --- 9.3.1: HF search ---
    HF_SEARCH_STARTED = "hf.search.started"
    HF_SEARCH_FOUND = "hf.search.found"
    HF_SEARCH_EMPTY = "hf.search.empty"
    HF_SEARCH_ERROR = "hf.search.error"

    # --- 9.4: git ---
    GIT_BRANCH_CHANGED = "git.branch.changed"
    GIT_COMMIT_CREATED = "git.commit.created"

    # --- 9.10: permissions dashboard ---
    PERMISSIONS_OVERRIDDEN = "permissions.overridden"
    PERMISSIONS_RESET = "permissions.reset"

    # --- Фаза 5 / 9.7: мультимодальность ---
    MEDIA_EXTRACTED = "media.extracted"
    MEDIA_ERROR = "media.error"

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


# ---------------------------------------------------------------------------
# 4.3: память
# ---------------------------------------------------------------------------


class MemoryRecalledPayload(TypedDict):
    query: str
    n_results: int
    ids: list[int]


class MemoryRememberedPayload(TypedDict):
    id: int
    task_type: str
    outcome: str
    tags: list[str]


# ---------------------------------------------------------------------------
# 7: MCP
# ---------------------------------------------------------------------------


class McpConnectedPayload(TypedDict):
    server_name: str
    n_tools: int
    server_info: dict


class McpDisconnectedPayload(TypedDict):
    server_name: str
    reason: NotRequired[str]


class McpToolCalledPayload(TypedDict):
    server_name: str
    tool_name: str
    duration_ms: float
    is_error: bool


class McpErrorPayload(TypedDict):
    server_name: str
    error: str


# ---------------------------------------------------------------------------
# 2.13: цепочка поиска
# ---------------------------------------------------------------------------


class SearchProviderUsedPayload(TypedDict):
    provider: str
    duration_ms: float
    n_results: int


class SearchChainExhaustedPayload(TypedDict):
    query: str
    attempted: list[str]
    errors: list[str]


# ---------------------------------------------------------------------------
# 2.12: durable inbox
# ---------------------------------------------------------------------------


class InboxEnqueuedPayload(TypedDict):
    id: str
    session_id: str
    mode: str


class InboxDeliveredPayload(TypedDict):
    id: str
    state: str  # "done"


class InboxFailedPayload(TypedDict):
    id: str
    error: str


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


# ---------------------------------------------------------------------------
# 2.5 + 2.10: бюджет агента и причина остановки
# ---------------------------------------------------------------------------


class BudgetSnapshotPayload(TypedDict):
    """Снимок ``SoftBudget`` — контракт для ``budget.warning/exceeded``
    и вложенного поля ``agent.finished["budget"]``.

    Совпадает с ``agents.agent_loop.SoftBudget.snapshot()`` 1:1.
    """

    iterations: int
    iterations_limit: int
    tokens: int
    tokens_limit: int
    time_s: float
    time_limit_s: float
    status: str  # "ok" | "warning" | "exceeded"


class AgentFinishedPayload(TypedDict):
    """Финальное событие прогона ``AgentLoop``.

    Attributes:
        finish_reason: ``"stop"`` | ``"length"`` | ``"budget.exceeded"``
            | ``"user_cancel"`` | ``None`` (не было стрима).
        budget: Снимок бюджета на момент остановки (может отсутствовать,
            если прогон падал до создания ``SoftBudget``).
        iterations: Фактические итерации.
        elapsed_s: Wall-clock, секунды.
    """

    finish_reason: NotRequired[str]
    budget: NotRequired[BudgetSnapshotPayload]
    iterations: int
    elapsed_s: float


# ---------------------------------------------------------------------------
# 9.3.1: HF search
# ---------------------------------------------------------------------------


class HfSearchStartedPayload(TypedDict):
    query: str
    filter: str


class HfSearchFoundPayload(TypedDict):
    query: str
    n_results: int
    duration_ms: float


class HfSearchEmptyPayload(TypedDict):
    query: str
    duration_ms: float


class HfSearchErrorPayload(TypedDict):
    query: str
    error: str


# ---------------------------------------------------------------------------
# 9.4: git
# ---------------------------------------------------------------------------


class GitBranchChangedPayload(TypedDict):
    branch: str
    hash: str


class GitCommitCreatedPayload(TypedDict):
    branch: str
    commit_hash: str
    files: list[str]
    message: str


# ---------------------------------------------------------------------------
# 9.10: permissions dashboard
# ---------------------------------------------------------------------------


class PermissionsOverriddenPayload(TypedDict):
    tool: str
    policy: str  # "auto" | "ask_first" | "excluded"
    scope: str  # "session"


class PermissionsResetPayload(TypedDict):
    tools: list[str]


# ---------------------------------------------------------------------------
# Фаза 5 / 9.7: медиа
# ---------------------------------------------------------------------------


class MediaExtractedPayload(TypedDict):
    path: str
    kind: str  # image / audio / video
    chars: int
    duration_ms: float
    cached: bool


class MediaErrorPayload(TypedDict):
    path: str
    kind: str
    error: str


__all__ = [
    "AgentFinishedPayload",
    "BudgetSnapshotPayload",
    "EventKind",
    "IndexFinishedPayload",
    "IndexProgressPayload",
    "IndexStartedPayload",
    "LlmFirstTokenPayload",
    "LlmGenerationDonePayload",
    "LlmRequestSentPayload",
    "LogPayload",
    "MemoryRecalledPayload",
    "MemoryRememberedPayload",
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
    "McpConnectedPayload",
    "McpDisconnectedPayload",
    "McpToolCalledPayload",
    "McpErrorPayload",
    "SearchProviderUsedPayload",
    "SearchChainExhaustedPayload",
    "InboxEnqueuedPayload",
    "InboxDeliveredPayload",
    "InboxFailedPayload",
    "HfSearchStartedPayload",
    "HfSearchFoundPayload",
    "HfSearchEmptyPayload",
    "HfSearchErrorPayload",
    "GitBranchChangedPayload",
    "GitCommitCreatedPayload",
    "PermissionsOverriddenPayload",
    "PermissionsResetPayload",
    "MediaExtractedPayload",
    "MediaErrorPayload",
    "args_hash",
]
