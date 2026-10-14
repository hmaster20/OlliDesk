# ui/chat/tool_registry_builder.py
"""Сборка ToolRegistry для агента с учётом режима и политик.

Заменяет `ChatPanel._build_registry` и `ChatPanel._apply_config_policies`.
Регистрация инструментов и применение политик вынесены в чистую функцию,
которая не знает о виджетах и не хранит состояние.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from agents.tool_registry import ToolRegistry
from core.config import AgentMode, ToolPolicy
from ui.chat.tool_labels import CHAT_AUTO_TOOLS


def _register_all(registry: ToolRegistry, *, with_attached_file: bool) -> None:
    """Регистрирует все инструменты агента.

    Args:
        registry: Пустой реестр.
        with_attached_file: True, если пользователь прикрепил файлы —
            тогда регистрируется инструмент `read_attached_file`.
    """
    # Импорты внутри функции — чтобы избежать циклической зависимости
    # на уровне модуля и ускорить старт приложения.
    from tools.codebase_search import search_codebase
    from tools.current_datetime import get_current_datetime
    from tools.exchange_rate import (
        get_exchange_rate,
        get_exchange_rates_multi,
    )
    from tools.file_ops import list_directory, read_file, write_file
    from tools.git_ops_tool import (
        tool_create_snapshot,
        tool_git_status,
        tool_undo_snapshot,
    )
    from tools.hypothesis import (
        list_hypotheses,
        record_hypothesis,
        update_hypothesis,
    )
    from tools.weather import get_weather
    from tools.web_search import web_search

    registry.register(read_file)
    registry.register(list_directory)
    registry.register(write_file)
    registry.register(search_codebase)
    registry.register(web_search)
    registry.register(get_weather)
    registry.register(get_current_datetime)
    registry.register(get_exchange_rate)
    registry.register(get_exchange_rates_multi)
    registry.register(tool_git_status)
    registry.register(tool_create_snapshot)
    registry.register(tool_undo_snapshot)

    if with_attached_file:
        from tools.attached_files import read_attached_file

        registry.register(read_attached_file)

    registry.register(record_hypothesis)
    registry.register(update_hypothesis)
    registry.register(list_hypotheses)


def _apply_config_policies(registry: ToolRegistry, config: Any) -> None:
    """Применяет политики из конфига и правило для web_search.

    Args:
        registry: Заполненный реестр.
        config: AppConfig (уже загруженный — не читаем YAML ещё раз).
    """
    for tool_name, policy in config.tool_policies.items():
        registry.set_policy(tool_name, policy)

    confirm = config.confirm
    if not (confirm.enabled or confirm.web_search):
        registry.set_policy("web_search", ToolPolicy.AUTO)


def build_registry(
    mode: AgentMode,
    *,
    attached_files: bool = False,
    tool_policies: dict[str, ToolPolicy] | None = None,
    config: Any = None,
) -> ToolRegistry:
    """Собирает реестр инструментов под указанный режим агента.

    Args:
        mode: Режим агента (CHAT / PLAN / AGENT).
        attached_files: Есть ли в сессии прикреплённые файлы.
        tool_policies: Переопределения политик из UI (галочки в правой панели).
        config: AppConfig; если ``None`` — читается через ``load_config()``.

    Returns:
        Готовый реестр с применёнными политиками.
    """
    registry = ToolRegistry()
    _register_all(registry, with_attached_file=attached_files)

    # В CHAT-режиме все инструменты, разрешённые для чата, работают без диалога.
    if mode == AgentMode.CHAT:
        for name in CHAT_AUTO_TOOLS:
            registry.set_policy(name, ToolPolicy.AUTO)

    # Политики из конфига (или переданного AppConfig).
    if config is None:
        try:
            from core.config import load_config

            config = load_config()
        except Exception as e:
            logger.warning(f"Не удалось прочитать конфиг для политик: {e}")
            config = None

    if config is not None:
        _apply_config_policies(registry, config)

    # Пользовательские переопределения из правой панели — приоритетнее.
    if tool_policies:
        for tool_name, policy in tool_policies.items():
            registry.set_policy(tool_name, policy)

    return registry