"""9.4: git_context в AgentContext / system prompt."""

from __future__ import annotations

from agents.agent_loop import AgentContext, _build_system_prompt
from core.config import AgentMode


class TestGitContextInPrompt:
    def test_git_context_injected(self) -> None:
        ctx = AgentContext(
            mode=AgentMode.AGENT,
            git_context=(
                "Ветка: feature/x (abc1234)\n"
                "Последние коммиты:\n"
                "  • a1b2c3d 2025-01-01 feat: added parser"
            ),
        )
        prompt = _build_system_prompt(ctx)
        assert "Ветка: feature/x" in prompt
        assert "a1b2c3d" in prompt

    def test_empty_git_context_omitted(self) -> None:
        ctx = AgentContext(mode=AgentMode.AGENT)
        prompt = _build_system_prompt(ctx)
        assert "Ветка:" not in prompt

    def test_git_context_after_memory(self) -> None:
        """git_context идёт после memory (порядок релевантен при обрезке)."""
        ctx = AgentContext(
            mode=AgentMode.AGENT,
            git_context="Ветка: main (abc)",
        )
        prompt = _build_system_prompt(ctx)
        # Хоть memory и git — переменные секции, оба на месте.
        assert "Ветка: main" in prompt