"""Сервис сборки `AgentContext` (2.8).

Полный вынос `ChatPanel._do_chat/_do_agent` — большая работа с высоким
риском регрессии. В этой итерации — только **сборка контекста** (без
создания `AgentChatThread`): тестируемо без Qt, легко подключить
к существующему `_do_chat` позже.

`ChatService.build_agent_context(snapshot, mode)`:
  * принимает «снимок» состояния ChatPanel (`ChatRequestSnapshot`);
  * возвращает готовый `AgentContext` — ровно то, что сейчас собирается
    вручную в 30 строк внутри `_do_chat/_do_agent`.

`ChatService.pick_max_iterations(mode, ...)`:
  * вынести `ChatPanel._iteration_limit` — он не зависит от Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.agent_loop import AgentContext
from agents.hypotheses import HypothesisStore
from core.config import AgentMode, AppConfig
from core.profiles import ProfileConfig
from core.security import SecurityPolicy


@dataclass
class ChatRequestSnapshot:
    """Снимок состояния ChatPanel для `build_agent_context`.

    Все поля — «чистые» данные или протоколы (без Qt). Собирается
    в `ChatPanel._do_chat/_do_agent` перед вызовом сервиса.
    """

    system_prompt: str = ""
    rag_context: str = ""
    agents_context: str = ""
    project_root: str = ""
    project_name: str = ""
    project_map: str = ""
    extra_rules: str = ""
    git_context: str = ""
    num_ctx: int = 0
    vector_store: Any = None
    search_cache: Any = None
    attached_files: Any = None
    hypotheses: HypothesisStore | None = None
    memory_service: Any = None
    profile: ProfileConfig | None = None
    security: SecurityPolicy | None = None
    app_config: AppConfig | None = None


class ChatService:
    """Сборка `AgentContext` и подбор `max_iterations` без Qt."""

    @staticmethod
    def build_agent_context(
        snapshot: ChatRequestSnapshot,
        mode: AgentMode,
    ) -> AgentContext:
        """Собирает `AgentContext` из снимка.

        Args:
            snapshot: Снимок состояния ChatPanel.
            mode: Режим агента (CHAT/PLAN/AGENT).

        Returns:
            `AgentContext` с заполненными полями. Поля, которых нет в
            снимке (``None``), остаются ``None``.
        """
        return AgentContext(
            system_prompt=snapshot.system_prompt,
            rag_context=snapshot.rag_context,
            agents_context=snapshot.agents_context,
            project_root=snapshot.project_root,
            project_name=snapshot.project_name,
            project_map=snapshot.project_map,
            vector_store=snapshot.vector_store,
            num_ctx=snapshot.num_ctx,
            mode=mode,
            search_cache=snapshot.search_cache,
            attached_files=snapshot.attached_files,
            hypotheses=snapshot.hypotheses,
            extra_rules=snapshot.extra_rules,
            security=snapshot.security,
            memory_service=snapshot.memory_service,
            profile=snapshot.profile,
            app_config=snapshot.app_config,
            git_context=snapshot.git_context,
        )

    @staticmethod
    def pick_max_iterations(
        mode: AgentMode,
        *,
        config: AppConfig | None = None,
        is_research: bool = False,
        profile: ProfileConfig | None = None,
        override: int | None = None,
    ) -> int:
        """Лимит итераций для режима и типа задачи.

        Логика (перенесена из `ChatPanel._iteration_limit` + `_do_agent`):

        * ``override`` задан — используется как есть (кнопки «Продолжить»).
        * AGENT → `profile.agent_max_iterations` или `config.agent_max_iterations`.
        * Research в CHAT/PLAN → max(15, min(agent_limit, agent_limit//2+8)).
        * Обычный CHAT/PLAN → `config.chat_max_iterations` (или
          `profile.chat_max_iterations`).

        Args:
            mode: Режим.
            config: `AppConfig` (для лимитов). ``None`` — безопасные
                дефолты.
            is_research: Research-вопрос.
            profile: Профиль (переопределяет лимиты).
            override: Явный лимит (кнопки «Продолжить»).
        """
        if override is not None:
            return override
        default_agent = 30
        default_chat = 7

        if mode == AgentMode.AGENT:
            if profile is not None:
                return int(profile.agent_max_iterations)
            if config is not None:
                return int(config.agent_max_iterations)
            return default_agent

        if is_research:
            agent_limit = (
                int(profile.agent_max_iterations)
                if profile is not None
                else int(config.agent_max_iterations)
                if config is not None
                else default_agent
            )
            return max(15, min(agent_limit, agent_limit // 2 + 8))

        if profile is not None:
            return int(profile.chat_max_iterations)
        if config is not None:
            return int(config.chat_max_iterations)
        return default_chat


__all__ = ["ChatRequestSnapshot", "ChatService"]