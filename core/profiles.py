"""Профили использования (Разработка / Исследование / Обучение) — 9.4.

Профиль — именованный пресет поведения агента. Применяется при
переключении в верхней панели чата. Влияет на:

  * `ToolPolicy` каждого инструмента (auto/ask_first/excluded);
  * `AgentMode` (chat/plan/agent);
  * флаг RAG (включён/выключен);
  * системный промпт профиля (дополнение к роли);
  * флаг «показывать размышления»;
  * рекомендуемые лимиты итераций.

Встроенные профили заданы в `_DEFAULT_PROFILES`. Пользовательские
профили хранятся в `AppConfig.profiles` (список `ProfileConfig`).
Поле `id` должно быть уникальным.

Не путать с `Role` (роль модели: chat/embed/apply) и `AgentMode`
(режим: chat/plan/agent) — это ортогональные вещи.
"""

from pydantic import BaseModel, Field, field_validator
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProfileToolPolicyStr = Literal["auto", "ask_first", "excluded"]


class ProfileToolPolicies(BaseModel):
    """Политики инструментов внутри профиля.

    Ключ — имя инструмента, значение — политика. Поля покрывают
    ВСЕ зарегистрированные в `tools/registry.py::ALL_TOOLS` инструменты,
    чтобы смена профиля применялась ко всем без исключения.
    """

    read_file: ProfileToolPolicyStr = "auto"
    list_directory: ProfileToolPolicyStr = "auto"
    search_codebase: ProfileToolPolicyStr = "auto"
    write_file: ProfileToolPolicyStr = "ask_first"
    web_search: ProfileToolPolicyStr = "ask_first"
    get_weather: ProfileToolPolicyStr = "auto"
    get_current_datetime: ProfileToolPolicyStr = "auto"
    get_exchange_rate: ProfileToolPolicyStr = "auto"
    get_exchange_rates_multi: ProfileToolPolicyStr = "auto"
    get_git_status: ProfileToolPolicyStr = "auto"
    create_snapshot: ProfileToolPolicyStr = "ask_first"
    undo_last_snapshot: ProfileToolPolicyStr = "ask_first"
    read_attached_file: ProfileToolPolicyStr = "auto"
    record_hypothesis: ProfileToolPolicyStr = "auto"
    update_hypothesis: ProfileToolPolicyStr = "auto"
    list_hypotheses: ProfileToolPolicyStr = "auto"
    run_shell_command: ProfileToolPolicyStr = "ask_first"


class ProfileConfig(BaseModel):
    """Один профиль использования.

    **9.3.2 + 4.1** — новые поля:
        * ``recommended_model`` — визуальная подсказка (звёздочка) в
          селекторах для профиля «Разработка»; на выбор не влияет.
        * ``park_roles`` — id моделей по ролям парка. Ключи — ``Role.value``
          (``"router"``, ``"planner"``, ``"coder"``, ``"verifier"``,
          ``"analyst"``). Значения — ``SLMModelInfo.id``. Пустой dict →
          автовыбор через ``agents.slm_selector.select_park``.
        * ``freedom_level`` — уровень свободы агента (9.12, будет позже).
    """

    id: str
    name: str
    icon: str = "🧩"
    description: str = ""
    builtin: bool = False

    agent_mode: Literal["chat", "plan", "agent"] = "agent"

    rag_enabled: bool = True
    rag_categories: list[Literal["code", "docs"]] = Field(default_factory=lambda: ["code"])

    system_prompt: str = ""

    show_reasoning: bool = True

    agent_max_iterations: int = 30
    chat_max_iterations: int = 7

    tool_policies: ProfileToolPolicies = Field(default_factory=ProfileToolPolicies)

    recommended_model: str = ""

    # 4.1: парк моделей по ролям.
    park_roles: dict[str, str] = Field(default_factory=dict)

    # 4.2: желаемое окно контекста для ролей парка.
    # 0 — не ограничивать (используем то, что даёт `optimize` по RAM/VRAM).
    # Для CPU-аналитика: 16384 (research) / 32768 (development) /
    # 65536 (deep). Не «жёсткий cap», а hint: оптимизатор берёт
    # `min(n_ctx_hint, budget_by_ram)`.
    n_ctx_hint: int = 8192

    # [10.2] Предлагать run_tests после правки кода. Влияет только на
    # подсказку в промпте (см. `_TOOL_USE_INSTRUCTION`); сам вызов
    # инструмента — на усмотрение модели, ASK_FIRST-подтверждение.
    run_tests_after_edit: bool = False

    @field_validator("n_ctx_hint")
    @classmethod
    def _validate_n_ctx_hint(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"n_ctx_hint must be >= 0, got {v}")
        return v

_DEFAULT_PROFILES: list[dict] = [
    {
        "id": "development",
        "name": "Разработка",
        "icon": "🛠",
        "builtin": True,
        "description": (
            "Полный набор инструментов, planner+analyst, RAG по коду. "
            "Запись файлов и git-снапшоты — с подтверждением. "
            "Профиль по умолчанию для работы с кодом проекта."
        ),
        "agent_mode": "agent",
        "rag_enabled": True,
        "rag_categories": ["code"],
        "show_reasoning": True,
        "agent_max_iterations": 30,
        "chat_max_iterations": 7,
        # Оптимум для кода (из _DEFAULT_SLM_MODELS). Подсвечивается ★ в
        # селекторе analyst; выбор пользователя не блокируется.
        "recommended_model": "qwen2.5-coder-7b-instruct-q6_k",
        # 4.1: парк по ролям. Router/planner/verifier — 3B на GPU,
        # coder — MoE-модель на CPU (или 14B, если MoE не скачана).
        "park_roles": {
            "router": "qwen2.5-3b-instruct-q4_k_m",
            "planner": "qwen2.5-3b-instruct-q4_k_m",
            "verifier": "qwen2.5-3b-instruct-q4_k_m",
            "coder": "qwen3-coder-30b-a3b-instruct-q4_k_m",
        },
        # 4.2: 32k — code-reading и apply_patch требуют большого окна.
        # Оптимизатор урежет до budget_by_ram, если RAM не хватит.
        "n_ctx_hint": 32768,
        "tool_policies": {
            "write_file": "ask_first",
            "create_snapshot": "ask_first",
            "undo_last_snapshot": "ask_first",
            "web_search": "ask_first",
            "run_shell_command": "ask_first",
        },
        "run_tests_after_edit": True,
    },
    {
        "id": "research",
        "name": "Исследование",
        "icon": "🔬",
        "builtin": True,
        "description": (
            "Акцент на документах: чтение, PDF/DOCX/OCR, веб-поиск, гипотезы. "
            "Запись файлов, git и shell — запрещены. RAG по документации "
            "и коду. Подходит для анализа статей, отчётов, ТЗ."
        ),
        "agent_mode": "plan",
        "rag_enabled": True,
        "rag_categories": ["docs", "code"],
        "show_reasoning": True,
        "agent_max_iterations": 20,
        "chat_max_iterations": 12,
        "system_prompt": (
            "Ты — исследователь. Твоя задача — собрать, проверить и "
            "структурировать факты из документов, кода и веб-источников. "
            "Для каждого утверждения указывай источник [Ei] или цитату. "
            "Не выдумывай — если факт не подтверждён, так и скажи."
        ),
        # Большой контекст под документы: q6_k 7B — баланс качества/памяти.
        "recommended_model": "qwen2.5-7b-instruct-q6_k",
        # Research: двухмодельный пайплайн как раньше (planner+analyst).
        "park_roles": {
            "planner": "qwen2.5-3b-instruct-q4_k_m",
            "analyst": "qwen2.5-7b-instruct-q5_k_m",
        },
        # 4.2: 16k — хватает на hypothesis-проверки и паспорт.
        "n_ctx_hint": 16384,
        "tool_policies": {
            "write_file": "excluded",
            "create_snapshot": "excluded",
            "undo_last_snapshot": "excluded",
            "run_shell_command": "excluded",
            "web_search": "auto",
            "record_hypothesis": "auto",
            "update_hypothesis": "auto",
            "list_hypotheses": "auto",
        },
        "run_tests_after_edit": False,
    },
    {
        "id": "learning",
        "name": "Обучение",
        "icon": "🎓",
        "builtin": True,
        "description": (
            "Простой чат без инструментов и ReAct. Модель отвечает "
            "на вопросы по общим темам, объясняет концепции, генерирует "
            "примеры. Использует крупную модель (analyst 7B/14B). "
            "Возврат в «Разработку» — одной кнопкой."
        ),
        "agent_mode": "chat",
        "rag_enabled": False,
        "rag_categories": [],
        "show_reasoning": False,
        "agent_max_iterations": 3,
        "chat_max_iterations": 5,
        "system_prompt": (
            "Ты — терпеливый преподаватель. Объясняй пошагово, с примерами "
            "и аналогиями. Проверяй понимание, задавай уточняющие вопросы. "
            "Не используй инструменты — отвечай из знаний."
        ),
        # Рекомендация «крупная модель для обучения» (см. 9.4.1): 14B q4_k_m
        # влезает в 32 ГБ RAM, качество заметно выше 7B.
        "recommended_model": "qwen2.5-14b-instruct-q4_k_m",
        # Learning: только analyst, никаких инструментов.
        "park_roles": {
            "analyst": "qwen2.5-14b-instruct-q4_k_m",
        },
        # 4.2: 8k — диалог без инструментов, длинная история не нужна.
        "n_ctx_hint": 8192,
        "tool_policies": {
            "read_file": "excluded",
            "list_directory": "excluded",
            "search_codebase": "excluded",
            "write_file": "excluded",
            "web_search": "excluded",
            "get_weather": "excluded",
            "get_current_datetime": "excluded",
            "get_exchange_rate": "excluded",
            "get_exchange_rates_multi": "excluded",
            "get_git_status": "excluded",
            "create_snapshot": "excluded",
            "undo_last_snapshot": "excluded",
            "read_attached_file": "excluded",
            "record_hypothesis": "excluded",
            "update_hypothesis": "excluded",
            "list_hypotheses": "excluded",
            "run_shell_command": "excluded",
        },
        "run_tests_after_edit": False,
    },
]


def default_profiles() -> list[ProfileConfig]:
    """Возвращает встроенные профили (свежая копия)."""
    return [ProfileConfig(**p) for p in _DEFAULT_PROFILES]


def builtin_profile_ids() -> set[str]:
    """ID встроенных профилей — для синхронизации с пользовательскими."""
    return {p["id"] for p in _DEFAULT_PROFILES}


def get_profile_by_id(profiles: list[ProfileConfig], profile_id: str) -> ProfileConfig | None:
    """Ищет профиль по id; None, если не найден."""
    for p in profiles:
        if p.id == profile_id:
            return p
    return None


def get_default_profile(profiles: list[ProfileConfig]) -> ProfileConfig:
    """Профиль по умолчанию (development) или первый в списке."""
    by_id = get_profile_by_id(profiles, "development")
    if by_id is not None:
        return by_id
    return profiles[0]


__all__ = [
    "ProfileConfig",
    "ProfileToolPolicies",
    "builtin_profile_ids",
    "default_profiles",
    "get_default_profile",
    "get_profile_by_id",
]
