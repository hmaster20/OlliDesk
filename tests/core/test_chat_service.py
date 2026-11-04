"""2.8: ChatService — build_agent_context + pick_max_iterations."""

from __future__ import annotations

from agents.agent_loop import AgentContext
from core.config import AgentMode, AppConfig
from core.profiles import ProfileConfig, ProfileToolPolicies
from core.services.chat_service import ChatRequestSnapshot, ChatService


class TestBuildAgentContext:
    def test_all_fields_passed(self) -> None:
        snapshot = ChatRequestSnapshot(
            system_prompt="sys",
            rag_context="rag",
            agents_context="agents",
            project_root="/proj",
            project_name="proj",
            project_map="map",
            extra_rules="rules",
            git_context="git",
            num_ctx=8192,
            vector_store="vs",
            search_cache="sc",
        )
        ctx = ChatService.build_agent_context(snapshot, AgentMode.AGENT)
        assert isinstance(ctx, AgentContext)
        assert ctx.system_prompt == "sys"
        assert ctx.rag_context == "rag"
        assert ctx.git_context == "git"
        assert ctx.mode == AgentMode.AGENT
        assert ctx.num_ctx == 8192

    def test_none_fields_ok(self) -> None:
        snapshot = ChatRequestSnapshot()
        ctx = ChatService.build_agent_context(snapshot, AgentMode.CHAT)
        assert ctx.vector_store is None
        assert ctx.project_root == ""


class TestPickMaxIterations:
    def test_override_wins(self) -> None:
        assert ChatService.pick_max_iterations(
            AgentMode.CHAT, override=42
        ) == 42

    def test_agent_uses_config(self) -> None:
        cfg = AppConfig(
            default_model="m", embed_model="e",
            agent_max_iterations=50, chat_max_iterations=10,
        )
        assert ChatService.pick_max_iterations(
            AgentMode.AGENT, config=cfg
        ) == 50

    def test_chat_uses_config(self) -> None:
        cfg = AppConfig(
            default_model="m", embed_model="e",
            agent_max_iterations=50, chat_max_iterations=10,
        )
        assert ChatService.pick_max_iterations(
            AgentMode.CHAT, config=cfg
        ) == 10

    def test_research_boosted(self) -> None:
        cfg = AppConfig(
            default_model="m", embed_model="e",
            agent_max_iterations=30, chat_max_iterations=7,
        )
        result = ChatService.pick_max_iterations(
            AgentMode.CHAT, config=cfg, is_research=True
        )
        assert result >= 15

    def test_profile_overrides(self) -> None:
        profile = ProfileConfig(
            id="p", name="P",
            agent_max_iterations=20, chat_max_iterations=5,
            tool_policies=ProfileToolPolicies(),
        )
        assert ChatService.pick_max_iterations(
            AgentMode.AGENT, profile=profile
        ) == 20