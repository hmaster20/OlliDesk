"""Конфигурация OlliDesk."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator

from core.exceptions import ConfigError
from core.profiles import (
    ProfileConfig,
    builtin_profile_ids,
    default_profiles,
    get_profile_by_id,
)


class ModelRole(StrEnum):
    """Роль модели в системе."""

    CHAT = "chat"
    EMBED = "embed"
    RERANK = "rerank"
    APPLY = "apply"
    AUTOCOMPLETE = "autocomplete"


class ToolPolicy(StrEnum):
    """Политика выполнения инструмента."""

    AUTO = "auto"  # Выполнять без подтверждения
    ASK_FIRST = "ask_first"  # Требовать подтверждения пользователя
    EXCLUDED = "excluded"  # Исключить из использования


class AgentMode(StrEnum):
    """Режим работы агента."""

    CHAT = "chat"  # Только диалог, без инструментов
    PLAN = "plan"  # Read-only инструменты
    AGENT = "agent"  # Все инструменты


class ModelConfig(BaseModel):
    """Конфигурация одной модели Ollama."""

    name: str
    provider: Literal["ollama", "openai", "openai-compatible"]
    model: str
    api_base: str = "http://localhost:11434"
    roles: list[ModelRole] = Field(default_factory=lambda: [ModelRole.CHAT])
    capabilities: list[str] = Field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 2048
    context_length: int = 8192
    keep_alive: int = 1800
    reasoning: bool = False

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        """temperature ∈ [0, 2]."""
        if not 0 <= v <= 2:
            raise ValueError(f"temperature must be in [0, 2], got {v}")
        return v

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int) -> int:
        """max_tokens > 0."""
        if v <= 0:
            raise ValueError(f"max_tokens must be > 0, got {v}")
        return v


class SLMModelInfo(BaseModel):
    """Описание одной доступной локальной SLM-модели (GGUF).

    kind="chat"  — используется как chat-модель (загружается с chat_format="chatml")
    kind="embed" — используется как embedding-модель (загружается с embedding=True)

    tier — уровень мощности (0..5), определяет приоритет при автовыборе:
        0 = микро (1.5B)      — CPU-only, ≤4 ГБ RAM
        1 = малый (3B)         — 4 ГБ VRAM
        2 = средний (7–9B)     — 6–8 ГБ VRAM / 16 ГБ RAM
        3 = крупный (14B)      — 10–12 ГБ VRAM / 32 ГБ RAM
        4 = XL (22–34B)        — 16–24 ГБ VRAM / 64 ГБ RAM
        5 = XXL (70–72B)       — 32+ ГБ VRAM / 96+ ГБ RAM

    n_ctx — тренировочный контекст модели в токенах (верхняя граница для
        оптимизатора). 0 = взять slm.target_n_ctx как ориентир.
    """

    id: str
    repo: str
    file: str
    display_name: str
    size_mb: int = 0
    kind: Literal["chat", "embed"] = "chat"
    # Окно контекста этой модели в токенах (0 = взять slm.n_ctx).
    # Задаётся per-model, потому что маленьким моделям KV-кэш дешёв,
    # а крупным — большой n_ctx упирается в VRAM/RAM.
    n_ctx: int = 0
    tier: int = 2

    @field_validator("tier")
    @classmethod
    def _validate_tier(cls, v: int) -> int:
        if not 0 <= v <= 5:
            raise ValueError(f"tier must be 0..5, got {v}")
        return v


_DEFAULT_SLM_MODELS: list[dict] = [
    # =====================================================================
    # TIER 0 — микро (1.5B): CPU, ≤4 ГБ RAM
    # =====================================================================
    {
        "id": "qwen2.5-1.5b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 1.5B q4_k_m · CPU / 4 ГБ RAM",
        "size_mb": 1120,
        "tier": 0,
        "n_ctx": 16384,
    },
    {
        "id": "qwen2.5-1.5b-instruct-q8_0",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "file": "qwen2.5-1.5b-instruct-q8_0.gguf",
        "display_name": "Qwen 2.5 1.5B q8_0 · почти без потерь",
        "size_mb": 1750,
        "tier": 0,
        "n_ctx": 16384,
    },
    # =====================================================================
    # TIER 1 — малые (3B): 4 ГБ VRAM
    # =====================================================================
    {
        "id": "qwen2.5-3b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "file": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 3B q4_k_m · 4 ГБ VRAM, ~50 tok/s",
        "size_mb": 1930,
        "tier": 1,
        "n_ctx": 16384,
    },
    {
        "id": "qwen2.5-3b-instruct-q6_k",
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "file": "qwen2.5-3b-instruct-q6_k.gguf",
        "display_name": "Qwen 2.5 3B q6_k · баланс для 4 ГБ VRAM",
        "size_mb": 2740,
        "tier": 1,
        "n_ctx": 16384,
    },
    {
        "id": "qwen2.5-coder-3b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        "file": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 Coder 3B q4_k_m · код, 4 ГБ VRAM",
        "size_mb": 1930,
        "tier": 1,
        "n_ctx": 16384,
    },
    {
        "id": "qwen2.5-coder-3b-instruct-q6_k",
        "repo": "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF",
        "file": "qwen2.5-coder-3b-instruct-q6_k.gguf",
        "display_name": "Qwen 2.5 Coder 3B q6_k · код, качественнее",
        "size_mb": 2740,
        "tier": 1,
        "n_ctx": 16384,
    },
    # =====================================================================
    # TIER 2 — средние (7–9B): 6–8 ГБ VRAM / 16 ГБ RAM
    # =====================================================================
    {
        "id": "qwen2.5-7b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "file": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 7B q4_k_m · минимум места",
        "size_mb": 4680,
        "tier": 2,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-7b-instruct-q5_k_m",
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "file": "qwen2.5-7b-instruct-q5_k_m.gguf",
        "display_name": "Qwen 2.5 7B q5_k_m · баланс",
        "size_mb": 5450,
        "tier": 2,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-7b-instruct-q6_k",
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "file": "qwen2.5-7b-instruct-q6_k.gguf",
        "display_name": "Qwen 2.5 7B q6_k ★ лучшее качество",
        "size_mb": 6300,
        "tier": 2,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-coder-7b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "file": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 Coder 7B q4_k_m · код, компактно",
        "size_mb": 4680,
        "tier": 2,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-coder-7b-instruct-q5_k_m",
        "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "file": "qwen2.5-coder-7b-instruct-q5_k_m.gguf",
        "display_name": "Qwen 2.5 Coder 7B q5_k_m · код, лучше",
        "size_mb": 5450,
        "tier": 2,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-coder-7b-instruct-q6_k",
        "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "file": "qwen2.5-coder-7b-instruct-q6_k.gguf",
        "display_name": "Qwen 2.5 Coder 7B q6_k ★ оптимум для кода",
        "size_mb": 6300,
        "tier": 2,
        "n_ctx": 32768,
    },
    {
        "id": "llama-3.1-8b-instruct-q4_k_m",
        "repo": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "display_name": "Llama 3.1 8B q4_k_m · англ., общие задачи",
        "size_mb": 4920,
        "tier": 2,
        "n_ctx": 32768,
    },
    {
        "id": "llama-3.1-8b-instruct-q5_k_m",
        "repo": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        "file": "Meta-Llama-3.1-8B-Instruct-Q5_K_M.gguf",
        "display_name": "Llama 3.1 8B q5_k_m",
        "size_mb": 5700,
        "tier": 2,
        "n_ctx": 32768,
    },
    {
        "id": "mistral-7b-instruct-v0.3-q5_k_m",
        "repo": "bartowski/Mistral-7B-Instruct-v0.3-GGUF",
        "file": "Mistral-7B-Instruct-v0.3-Q5_K_M.gguf",
        "display_name": "Mistral 7B v0.3 q5_k_m · быстрая, англ.",
        "size_mb": 5400,
        "tier": 2,
        "n_ctx": 32768,
    },
    # =====================================================================
    # TIER 3 — крупные (14B): 10–12 ГБ VRAM / 32 ГБ RAM
    # =====================================================================
    {
        "id": "qwen2.5-14b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-14B-Instruct-GGUF",
        "file": "qwen2.5-14b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 14B q4_k_m",
        "size_mb": 9000,
        "tier": 3,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-14b-instruct-q5_k_m",
        "repo": "Qwen/Qwen2.5-14B-Instruct-GGUF",
        "file": "qwen2.5-14b-instruct-q5_k_m.gguf",
        "display_name": "Qwen 2.5 14B q5_k_m · заметно умнее q4",
        "size_mb": 10500,
        "tier": 3,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-14b-instruct-q6_k",
        "repo": "Qwen/Qwen2.5-14B-Instruct-GGUF",
        "file": "qwen2.5-14b-instruct-q6_k.gguf",
        "display_name": "Qwen 2.5 14B q6_k",
        "size_mb": 12200,
        "tier": 3,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-coder-14b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        "file": "qwen2.5-coder-14b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 Coder 14B q4_k_m · код + контекст",
        "size_mb": 9000,
        "tier": 3,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-coder-14b-instruct-q5_k_m",
        "repo": "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        "file": "qwen2.5-coder-14b-instruct-q5_k_m.gguf",
        "display_name": "Qwen 2.5 Coder 14B q5_k_m ★ сильный код",
        "size_mb": 10500,
        "tier": 3,
        "n_ctx": 32768,
    },
    # =====================================================================
    # TIER 4 — XL (22–34B): 16–24 ГБ VRAM / 64 ГБ RAM
    # =====================================================================
    {
        "id": "mistral-small-22b-instruct-q4_k_m",
        "repo": "bartowski/mistralai_Mistral-Small-22B-Instruct-2409-GGUF",
        "file": "Mistral-Small-22B-Instruct-2409-Q4_K_M.gguf",
        "display_name": "Mistral Small 22B q4_k_m · 16 ГБ VRAM",
        "size_mb": 13400,
        "tier": 4,
        "n_ctx": 32768,
    },
    {
        "id": "gemma-2-27b-instruct-q4_k_m",
        "repo": "bartowski/gemma-2-27b-it-GGUF",
        "file": "gemma-2-27b-it-Q4_K_M.gguf",
        "display_name": "Gemma 2 27B q4_k_m · 16 ГБ VRAM",
        "size_mb": 16500,
        "tier": 4,
        "n_ctx": 8192,
    },
    {
        "id": "qwen2.5-32b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-32B-Instruct-GGUF",
        "file": "qwen2.5-32b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 32B q4_k_m · 20 ГБ VRAM",
        "size_mb": 19500,
        "tier": 4,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-32b-instruct-q5_k_m",
        "repo": "Qwen/Qwen2.5-32B-Instruct-GGUF",
        "file": "qwen2.5-32b-instruct-q5_k_m.gguf",
        "display_name": "Qwen 2.5 32B q5_k_m · 24 ГБ VRAM",
        "size_mb": 22500,
        "tier": 4,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-coder-32b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-Coder-32B-Instruct-GGUF",
        "file": "qwen2.5-coder-32b-instruct-q4_k_m.gguf",
        "display_name": "Qwen 2.5 Coder 32B q4_k_m ★ топ для кода",
        "size_mb": 19500,
        "tier": 4,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-coder-32b-instruct-q5_k_m",
        "repo": "Qwen/Qwen2.5-Coder-32B-Instruct-GGUF",
        "file": "qwen2.5-coder-32b-instruct-q5_k_m.gguf",
        "display_name": "Qwen 2.5 Coder 32B q5_k_m",
        "size_mb": 22500,
        "tier": 4,
        "n_ctx": 32768,
    },
    {
        "id": "yi-34b-chat-q4_k_m",
        "repo": "TheBloke/Yi-34B-Chat-GGUF",
        "file": "yi-34b-chat.Q4_K_M.gguf",
        "display_name": "Yi 34B Chat q4_k_m · альтернатива Qwen",
        "size_mb": 20100,
        "tier": 4,
        "n_ctx": 4096,
    },
    # =====================================================================
    # TIER 5 — XXL (70–72B): 32+ ГБ VRAM / 96+ ГБ RAM
    # =====================================================================
    {
        "id": "llama-3.1-70b-instruct-q4_k_m",
        "repo": "bartowski/Meta-Llama-3.1-70B-Instruct-GGUF",
        "file": "Meta-Llama-3.1-70B-Instruct-Q4_K_M.gguf",
        "display_name": "Llama 3.1 70B q4_k_m · топ на 32+ ГБ VRAM",
        "size_mb": 42500,
        "tier": 5,
        "n_ctx": 32768,
    },
    {
        "id": "qwen2.5-72b-instruct-q4_k_m",
        "repo": "Qwen/Qwen2.5-72B-Instruct-GGUF",
        "file": "qwen2.5-72b-instruct-q4_k_m-00001-of-00002.gguf",
        "display_name": "Qwen 2.5 72B q4_k_m · топ качества",
        "size_mb": 47400,
        "tier": 5,
        "n_ctx": 32768,
    },
]


class SLMConfig(BaseModel):
    """Конфигурация встроенной SLM (llama.cpp)."""

    enabled: bool = True

    # Активная chat-модель (id из available_models, kind="chat").
    # При auto_select_model=True значение перезаписывается автоматически.
    selected_model: str = "qwen2.5-3b-instruct-q4_k_m"

    # Явный выбор моделей для двухмодельного пайплайна (planner + analyst).
    # Пустая строка = автовыбор через agents.slm_selector.select_pair().
    # Заполняется в SLMModelsDialog (View → Локальные модели).
    # Приоритет над автовыбором: если оба id заданы — используется ровно
    # этот набор, даже если есть более подходящие по tier/размеру модели.
    planner_model_id: str = ""
    analyst_model_id: str = ""

    # Каталог доступных моделей.
    # Embedding-модель сюда НЕ добавляем — она задаётся отдельно ниже.
    available_models: list[SLMModelInfo] = Field(
        default_factory=lambda: [SLMModelInfo(**m) for m in _DEFAULT_SLM_MODELS]
    )

    @model_validator(mode="after")
    def _sync_builtin_models(self) -> "SLMConfig":
        """Обновляет встроенные модели до актуального каталога.

        Сохранённый в config.yaml список мог устареть. Метаданные встроенных
        моделей берём из кода, пользовательские оставляем как есть.
        """
        default_ids = {d["id"] for d in _DEFAULT_SLM_MODELS}
        custom = [m for m in self.available_models if m.id not in default_ids]
        self.available_models = [SLMModelInfo(**d) for d in _DEFAULT_SLM_MODELS] + custom
        return self

    # --- Режим автоматической оптимизации ---
    auto_select_model: bool = True
    auto_tune: bool = True
    prefer_vram_fit: bool = True

    # --- Ручные параметры (используются при auto_tune=False) ---
    n_ctx: int = 8192
    n_threads: int = 0
    n_batch: int = 512
    auto_gpu_layers: bool = True
    n_gpu_layers: int = 0
    max_tokens: int = 1024
    temperature: float = 0.7
    target_n_ctx: int = 8192

    # --- Embedding-модель (одна, отдельно) ---
    embed_model_repo: str = "nomic-ai/nomic-embed-text-v1.5-GGUF"
    embed_model_file: str = "nomic-embed-text-v1.5.Q4_K_M.gguf"

    # --- Хелперы ---

    def get_chat_models(self) -> list[SLMModelInfo]:
        """Только chat-модели (фильтрует embed)."""
        return [m for m in self.available_models if m.kind == "chat"]

    def get_selected_info(self) -> SLMModelInfo | None:
        """SLMModelInfo активной chat-модели.

        Ищем по id среди chat-моделей. Если не нашли — отдаём первую chat.
        Никогда не возвращаем embed-модель.
        """
        for m in self.available_models:
            if m.kind == "chat" and m.id == self.selected_model:
                return m
        for m in self.available_models:
            if m.kind == "chat":
                return m
        return None

    def get_model_by_id(self, model_id: str) -> SLMModelInfo | None:
        """Модель по id (любого kind). Для UI-таблицы."""
        for m in self.available_models:
            if m.id == model_id:
                return m
        return None

    def get_planner_info(self) -> SLMModelInfo | None:
        """Явно выбранная planner-модель для двухмодельного пайплайна.

        Returns:
            SLMModelInfo, если ``planner_model_id`` задан и модель существует
            и имеет ``kind="chat"``; иначе ``None`` (сигнал для автовыбора).
        """
        if not self.planner_model_id:
            return None
        for m in self.available_models:
            if m.id == self.planner_model_id and m.kind == "chat":
                return m
        return None

    def get_analyst_info(self) -> SLMModelInfo | None:
        """Явно выбранная analyst-модель для двухмодельного пайплайна.

        Returns:
            SLMModelInfo, если ``analyst_model_id`` задан и модель существует
            и имеет ``kind="chat"``; иначе ``None`` (сигнал для автовыбора).
        """
        if not self.analyst_model_id:
            return None
        for m in self.available_models:
            if m.id == self.analyst_model_id and m.kind == "chat":
                return m
        return None


class ConfirmSettings(BaseModel):
    """Настройки подтверждения вызова инструментов.

    По умолчанию web_search выполняется без диалога: раздражает подтверждать
    каждый сетевой запрос. Диалог включается явно через confirm.web_search или
    общий флаг confirm.enabled.
    """

    enabled: bool = False
    web_search: bool = False


class BackendSettings(BaseModel):
    """Выбор LLM-бэкендов."""

    enabled: list[str] = Field(default_factory=lambda: ["local_slm"])
    priority: list[str] = Field(default_factory=lambda: ["local_slm", "ollama"])
    embed_backend: str = "auto"

    @field_validator("enabled")
    @classmethod
    def validate_enabled(cls, v: list[str]) -> list[str]:
        allowed = {"ollama", "local_slm"}
        unknown = [b for b in v if b not in allowed]
        if unknown:
            raise ValueError(f"Неизвестные бэкенды в enabled: {unknown}")
        seen: list[str] = []
        for b in v:
            if b not in seen:
                seen.append(b)
        if "local_slm" not in seen:
            seen.insert(0, "local_slm")
        return seen

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: list[str]) -> list[str]:
        """Проверяет и дедуплицирует порядок бэкендов.

        НЕ добавляет `local_slm` автоматически (в отличие от `enabled`):
        priority — это явный порядок предпочтения, пользователь имеет право
        не указывать SLM, если её нет в enabled.
        """
        allowed = {"ollama", "local_slm"}
        unknown = [b for b in v if b not in allowed]
        if unknown:
            raise ValueError(f"Неизвестные бэкенды в priority: {unknown}")
        seen: list[str] = []
        for b in v:
            if b not in seen:
                seen.append(b)
        return seen

    @field_validator("embed_backend")
    @classmethod
    def validate_embed_backend(cls, v: str) -> str:
        """Один из auto/ollama/local_slm."""
        allowed = {"auto", "ollama", "local_slm"}
        if v not in allowed:
            raise ValueError(f"embed_backend должен быть одним из {sorted(allowed)}, got {v!r}")
        return v


class RerankSettings(BaseModel):
    """Реранкинг RAG-выдачи кросс-энкодером (sentence-transformers).

    Кросс-энкодер оценивает пару (запрос, чанк) совместно и точнее би-энкодера
    векторного поиска, но считается на CPU, а модель скачивается при первом
    использовании. Поэтому по умолчанию выключен и включается явно.

    Attributes:
        enabled: Включён ли реранкинг.
        model: HF-id модели-кросс-энкодера (например, ``BAAI/bge-reranker-v2-m3``).
        top_k: Сколько лучших кандидатов после RRF подавать на реранк.
    """

    enabled: bool = False
    model: str = "BAAI/bge-reranker-v2-m3"
    top_k: int = 20

    @field_validator("top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"top_k must be >= 1, got {v}")
        return v


class EmbedModels(BaseModel):
    """Раздельные модели эмбеддингов по категории файлов.

    Позволяет обслуживать код и документацию разными моделями (например,
    code-специализированной для кода и текстовой для markdown). Векторы разных
    моделей несравнимы, поэтому каждая модель получает свою коллекцию, а выдачи
    объединяются через RRF (`fs.vector_store.VectorStore`). Пустое значение
    категории означает общий ``embed_model``.

    Attributes:
        code: Модель для кода (python, javascript, …); ``None`` — ``embed_model``.
        docs: Модель для документации (markdown, rst, text); ``None`` — ``embed_model``.
    """

    code: str | None = None
    docs: str | None = None


CURRENT_SCHEMA_VERSION = "v2"

LEGACY_INDEX_EXTENSIONS: list[str] = [
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".md",
    ".json",
    ".yaml",
    ".yml",
]

DEFAULT_INDEX_EXTENSIONS: list[str] = [
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".astro",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".csv",
    ".json",
    ".jsonc",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".xml",
    ".properties",
    ".cs",
    ".csproj",
    ".sln",
    ".fs",
    ".vb",
    ".xaml",
    ".cshtml",
    ".razor",
    ".config",
    ".resx",
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".groovy",
    ".gradle",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".pl",
    ".lua",
    ".r",
    ".dart",
    ".swift",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".hpp",
    ".hh",
    ".m",
    ".mm",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".sql",
    ".tf",
    ".tfvars",
    ".hcl",
    ".pkr.hcl",
    # [9.4] Документы — для профиля «Исследование».
    # Экстрактор `fs/document_extractors.py` читает их через ленивые
    # библиотеки; без установленного `[docs]`-extra просто пропускаются.
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".rtf",
]

# Файлы БЕЗ расширения, индексируемые по имени.
# Ansible-плейбуки site.yml/playbook.yml распознаются ещё и по .yml выше,
# но Vagrantfile/Rakefile/Gemfile/Dockerfile/Makefile — только по имени.
DEFAULT_INDEX_FILENAMES: list[str] = [
    "Vagrantfile",
    "Rakefile",
    "Gemfile",
    "Guardfile",
    "Podfile",
    "Dockerfile",
    "Makefile",
    "CMakeLists.txt",
    "Playbook",
    "site.yml",
    "playbook.yml",
]

_GLOBAL_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "slm",
        "backends",
        "confirm",
        "rerank",
        "embed_models",
        "recent_projects",
        "wizard_dismissed",
        "theme",
        "last_model",
        "last_mode",
        "agent_max_iterations",
        "chat_max_iterations",
        # [9.4] Профили — глобальные, не per-project.
        "profiles",
        "active_profile",
        # [9.4] Документы — общий индекс, не per-project.
        "index_documents",
        "max_document_size_mb",
        "document_ocr",
        # [9.8] Поведение редактора — глобальное, не per-project.
        "open_modified_as_diff",
        # [0.1.1] Dangerous mode — глобальная политика безопасности.
        "allow_arbitrary_shell",
    }
)


class AppConfig(BaseModel):
    """Главная конфигурация приложения."""

    version: str = "1.0.0"
    schema_version: str = CURRENT_SCHEMA_VERSION
    default_model: str
    embed_model: str
    embed_models: EmbedModels = Field(default_factory=EmbedModels)
    models: list[ModelConfig] = Field(default_factory=list)
    tool_policies: dict[str, ToolPolicy] = Field(default_factory=dict)
    # Инструменты, которые в режиме CHAT выполняются без подтверждения.
    # Раньше список жил в `ui/chat/panel.py::_CHAT_AUTO_TOOLS` — теперь он
    # в конфиге: пользователь может дополнить его своим инструментом (например,
    # read-only плагином), не пересобирая приложение. Проектный `.ollidesk/
    # config.yaml` может переопределить список — так же, как `tool_policies`.
    chat_auto_tools: list[str] = Field(
        default_factory=lambda: [
            "web_search",
            "read_file",
            "search_codebase",
            "list_directory",
            "get_weather",
            "get_current_datetime",
            "get_exchange_rate",
            "get_exchange_rates_multi",
            "get_git_status",
            "read_attached_file",
            "record_hypothesis",
            "update_hypothesis",
            "list_hypotheses",
        ]
    )
    confirm: ConfirmSettings = Field(default_factory=ConfirmSettings)
    backends: BackendSettings = Field(default_factory=BackendSettings)
    rerank: RerankSettings = Field(default_factory=RerankSettings)
    slm: SLMConfig = Field(default_factory=SLMConfig)
    ignored_patterns: list[str] = Field(
        default_factory=lambda: [
            ".git",
            ".gitignore",
            ".gitattributes",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".cache",
            ".tox",
            "venv",
            ".venv",
            "env",
            ".env",
            "dist",
            "build",
            "target",
            "*.egg-info",
            ".eggs",
            "*.pyc",
            "*.pyo",
            "*.pyd",
            "*.min.js",
            "*.min.css",
            ".DS_Store",
            "Thumbs.db",
            ".ollidesk",
            ".ollidesk/vector_db",
            ".ollidesk/chroma",
            ".ollidesk/*.sqlite3",
            ".ollidesk/*.db",
            "logs",
            "log",
            "tmp",
            "temp",
            "vendor",
        ]
    )
    index_extensions: list[str] = Field(default_factory=lambda: list(DEFAULT_INDEX_EXTENSIONS))
    index_filenames: list[str] = Field(default_factory=lambda: list(DEFAULT_INDEX_FILENAMES))
    max_file_size_kb: int = 100

    # [9.4] Документы (PDF/DOCX/PPTX/XLSX/RTF) для профиля «Исследование».
    index_documents: bool = True
    max_document_size_mb: int = 50
    document_ocr: bool = False  # OCR сканов — дорого, включается явно
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 50
    rules_file: str = ".ollidesk/rules.md"
    recent_projects: list[str] = Field(default_factory=list)
    wizard_dismissed: bool = False
    theme: str = "light"
    last_model: str = ""
    last_mode: str = "agent"
    agent_max_iterations: int = 30
    chat_max_iterations: int = 7

    # [2.10] Мягкие бюджеты цикла агента. Жёсткий `max_iterations`
    # больше не основной выход — `AgentLoop` идёт до `finish_reason=stop`
    # или `user_cancel`, а бюджеты сигналят о «дорого/долго».
    # `soft_budget_iterations` — safety net для AGENT; для CHAT/PLAN
    # переопределяется `chat_max_iterations`/`agent_max_iterations`.
    soft_budget_time_s: int = 600
    soft_budget_tokens: int = 100_000
    soft_budget_iterations: int = 100

    @field_validator("soft_budget_time_s", "soft_budget_tokens", "soft_budget_iterations")
    @classmethod
    def validate_soft_budget(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"soft_budget must be >= 1, got {v}")
        return v

    # [9.2] Видимость дерева проекта (переключается кнопкой «📁» в верхней
    # панели чата и F4). Дефолт — False, дерево скрыто.
    show_project_tree: bool = False
    # [9.8] Открывать diff (HEAD ↔ рабочая копия) вместо самого файла,
    # когда пользователь кликает по ИЗМЕНЁННОМУ файлу в дереве проекта.
    # По умолчанию False — обычное открытие; diff доступен через
    # контекстное меню и Ctrl+D в редакторе.
    open_modified_as_diff: bool = False
    # [0.1.1] Разрешить произвольные shell-команды (dangerous mode).
    # По умолчанию False — run_shell_command работает только по allowlist
    # (Test-Path, Get-Content, git status, python -m pytest, …).
    # Включается через ⚙ → Безопасность или config.yaml. Даже при включении
    # чёрный список (rm -rf, format, …) продолжает блокировать.
    allow_arbitrary_shell: bool = False
    # [9.3] Показывать ли верхнюю панель со статусами Planner/Analyst.
    show_pipeline_selectors: bool = True
    # [9.4] Профили использования.
    profiles: list[ProfileConfig] = Field(default_factory=default_profiles)
    active_profile: str = "development"

    @field_validator("recent_projects")
    @classmethod
    def validate_recent_projects(cls, v: list[str]) -> list[str]:
        return v[:10]

    @field_validator("agent_max_iterations", "chat_max_iterations")
    @classmethod
    def validate_iterations(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"iterations must be >= 1, got {v}")
        return v

    @field_validator("max_file_size_kb")
    @classmethod
    def validate_max_file_size(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"max_file_size_kb must be > 0, got {v}")
        return v

    @field_validator("max_document_size_mb")
    @classmethod
    def validate_max_document_size(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"max_document_size_mb must be > 0, got {v}")
        if v > 500:
            raise ValueError(f"max_document_size_mb must be <= 500, got {v}")
        return v

    # [9.4] Синхронизация встроенных профилей с кодом.
    @model_validator(mode="after")
    def _sync_builtin_profiles(self) -> "AppConfig":
        """Обновляет встроенные профили до актуального набора.

        Пользовательские профили (`builtin=False`) сохраняются как есть.
        `active_profile` приводится к существующему id.
        """
        builtin_ids = builtin_profile_ids()
        custom = [p for p in self.profiles if p.id not in builtin_ids]
        self.profiles = default_profiles() + custom
        if get_profile_by_id(self.profiles, self.active_profile) is None:
            self.active_profile = "development"
        return self


class ConfigLoader:
    """Загрузчик и мержер конфигурации."""

    def __init__(self) -> None:
        from core.utils import get_app_data_dir

        self.global_config_path = get_app_data_dir() / "config.yaml"

    def load_config(self, project_path: Path | None = None) -> AppConfig:
        global_config = self._load_yaml(self.global_config_path)
        if project_path:
            local_config_path = project_path / ".ollidesk" / "config.yaml"
            local_config = self._load_yaml(local_config_path)
            merged_config = self._merge_configs(global_config, local_config)
        else:
            merged_config = global_config
        self._migrate_config(merged_config)
        try:
            return AppConfig(**merged_config)
        except Exception as e:
            raise ConfigError(f"Ошибка валидации конфигурации: {e}") from e

    def _migrate_config(self, config: dict[str, Any]) -> None:
        """Приводит устаревший конфиг к текущей схеме (in-place).

        Схема ``v1`` содержала урезанный список ``index_extensions`` без C#,
        Java, Go и прочих языков. Если в конфиге остался ровно старый набор по
        умолчанию, заменяем его на новый — но пользовательский список не трогаем.
        """
        if config.get("schema_version") == CURRENT_SCHEMA_VERSION:
            return
        extensions = config.get("index_extensions")
        if extensions is None or set(extensions) == set(LEGACY_INDEX_EXTENSIONS):
            config["index_extensions"] = list(DEFAULT_INDEX_EXTENSIONS)
        config["schema_version"] = CURRENT_SCHEMA_VERSION

    def _load_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.debug(f"Конфиг не найден: {path}")
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data if data else {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Ошибка парсинга YAML {path}: {e}") from e

    def _merge_configs(self, global_config: dict[str, Any], local_config: dict[str, Any]) -> dict[str, Any]:
        """Мержит два конфига, приоритет — за локальным.

        Ключи из ``_GLOBAL_ONLY_KEYS`` (SLM, бэкенды, тема, последние
        модель/режим и т. п.) описывают машину, а не проект, поэтому
        из локального конфига они молча игнорируются. Без этого старый
        шаблон `.ollidesk/config.yaml` в проекте перекрывал бы глобальный
        выбор активной SLM, и переключение 7B → 14B не срабатывало.
        """
        merged = global_config.copy()
        for key, value in local_config.items():
            if key in _GLOBAL_ONLY_KEYS:
                logger.debug(f"Локальный конфиг: игнорирую глобальный ключ {key!r}")
                continue
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = {**merged[key], **value}
            elif key in merged and isinstance(merged[key], list) and isinstance(value, list):
                merged[key] = value
            else:
                merged[key] = value
        return merged

    def save_config(self, config: AppConfig, project_path: Path | None = None) -> None:
        logger.info(
            f"💾 Сохранение конфига: last_model='{config.last_model}', "
            f"last_mode='{config.last_mode}', project_path={project_path}"
        )
        config_dict = config.model_dump(mode="json")
        if project_path:
            local_config_path = project_path / ".ollidesk" / "config.yaml"
            self._save_yaml(config_dict, local_config_path)
        else:
            self._save_yaml(config_dict, self.global_config_path)

    def _save_yaml(self, data: dict[str, Any], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            logger.info(f"Конфигурация сохранена: {path}")
        except Exception as e:
            raise ConfigError(f"Ошибка сохранения конфигурации {path}: {e}") from e


config_loader = ConfigLoader()


def _detect_project_path() -> Path | None:
    cwd = Path.cwd()
    if (cwd / ".ollidesk" / "config.yaml").exists():
        return cwd
    return None


def load_config(project_path: Path | None = None) -> AppConfig:
    if project_path is None:
        project_path = _detect_project_path()
    return config_loader.load_config(project_path)


def save_config(config: AppConfig, project_path: Path | None = None) -> None:
    config_loader.save_config(config, project_path)
    logger.info(f"Сохранение конфига: last_model={config.last_model}, last_mode={config.last_mode}")


def get_model_by_role(config: AppConfig, role: ModelRole) -> ModelConfig:
    for model in config.models:
        if role in model.roles:
            return model
    raise ConfigError(f"Модель с ролью {role.value} не найдена в конфигурации")
