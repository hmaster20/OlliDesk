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
    """Один профиль использования."""

    id: str
    name: str
    icon: str = "🧩"
    description: str = ""
    builtin: bool = False  # встроенный — нельзя удалить, можно клонировать

    # Режим агента по умолчанию для этого профиля.
    agent_mode: Literal["chat", "plan", "agent"] = "agent"

    # RAG.
    rag_enabled: bool = True
    # Категории RAG (code / docs). Полная фильтрация по категориям —
    # Фаза 5; сейчас флаг работает как «включить/выключить RAG целиком».
    rag_categories: list[Literal["code", "docs"]] = Field(default_factory=lambda: ["code"])

    # Промпт профиля (дополнение к роли, идёт в extra_rules).
    system_prompt: str = ""

    # Показывать размышления модели.
    show_reasoning: bool = True

    # Рекомендуемые лимиты итераций.
    agent_max_iterations: int = 30
    chat_max_iterations: int = 7

    # Политики инструментов.
    tool_policies: ProfileToolPolicies = Field(default_factory=ProfileToolPolicies)

    # Рекомендуемая модель (не блокирует выбор пользователя).
    recommended_model: str = ""


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
        "tool_policies": {
            "write_file": "ask_first",
            "create_snapshot": "ask_first",
            "undo_last_snapshot": "ask_first",
            "web_search": "ask_first",
            "run_shell_command": "ask_first",
        },
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
