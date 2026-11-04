"""9.10: explain_policy — пошаговое объяснение."""

from __future__ import annotations

import pytest

from agents.tool_registry import ToolSpec
from core.config import AgentMode, AppConfig, ToolPolicy
from core.policy import PolicyStep, explain_policy
from core.profiles import ProfileConfig, ProfileToolPolicies
from core.security import SecurityPolicy


def _spec(
    name: str = "read_file",
    caps: list[str] | None = None,
    *,
    policy: ToolPolicy = ToolPolicy.AUTO,
    modes: frozenset[AgentMode] = frozenset(
        {AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT}
    ),
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="",
        schema={},
        modes=modes,
        policy=policy,
        capabilities=frozenset(caps or ["fs.read"]),
        is_mutating=False,
        category="filesystem",
    )


def _profile(freedom: str = "development", **policies) -> ProfileConfig:
    return ProfileConfig(
        id="p",
        name="P",
        freedom_level=freedom,  # type: ignore[arg-type]
        tool_policies=ProfileToolPolicies(**policies),
    )


class TestExplainSecrets:
    def test_secrets_blocks_first(self) -> None:
        eff, steps = explain_policy(
            _spec(caps=["secrets"]), profile=_profile("open")
        )
        assert eff.policy == ToolPolicy.EXCLUDED
        assert steps[0].layer == "secrets"
        assert steps[0].blocking is True


class TestExplainConfidential:
    def test_confidential_blocks_net(self) -> None:
        sec = SecurityPolicy(confidential=True)
        eff, steps = explain_policy(
            _spec(name="web_search", caps=["net"]), security=sec
        )
        assert eff.policy == ToolPolicy.EXCLUDED
        layers = [s.layer for s in steps]
        assert "security" in layers
        sec_step = next(s for s in steps if s.layer == "security")
        assert sec_step.blocking is True


class TestExplainMode:
    def test_wrong_mode(self) -> None:
        spec = _spec(modes=frozenset({AgentMode.AGENT}))
        eff, steps = explain_policy(spec, current_mode=AgentMode.CHAT)
        assert eff.policy == ToolPolicy.EXCLUDED
        assert any(
            s.layer == "mode" and s.blocking for s in steps
        )


class TestExplainFreedom:
    def test_freedom_excludes_shell(self) -> None:
        eff, steps = explain_policy(
            _spec(name="run_shell_command", caps=["shell"]),
            profile=_profile("safe"),
        )
        assert eff.policy == ToolPolicy.EXCLUDED
        freedom_step = next(s for s in steps if s.layer == "freedom")
        assert freedom_step.blocking is True


class TestExplainProfile:
    def test_profile_override(self) -> None:
        eff, steps = explain_policy(
            _spec(name="write_file", caps=["fs.write"]),
            profile=_profile(
                "development", write_file="excluded"
            ),
        )
        assert eff.policy == ToolPolicy.EXCLUDED
        profile_step = next(
            s for s in steps if s.layer == "profile"
        )
        assert profile_step.applied is True
        assert profile_step.blocking is True


class TestExplainSession:
    def test_session_override(self) -> None:
        eff, steps = explain_policy(
            _spec(name="write_file", caps=["fs.write"]),
            profile=_profile("development"),
            session_overrides={"write_file": ToolPolicy.AUTO},
        )
        assert eff.policy == ToolPolicy.AUTO
        session_step = next(
            s for s in steps if s.layer == "session"
        )
        assert session_step.applied is True


class TestExplainConfig:
    def test_config_override(self) -> None:
        cfg = AppConfig(
            default_model="m",
            embed_model="e",
            tool_policies={"write_file": ToolPolicy.ASK_FIRST},
        )
        # profile.tool_policies задаст дефолт write_file=ask_first — этот
        # слой выигрывает по приоритету. Поэтому проверяем, что steps
        # содержит config (но не блокирует).
        eff, steps = explain_policy(
            _spec(name="write_file", caps=["fs.write"]),
            profile=_profile("development", write_file="auto"),
            config=cfg,
        )
        layers = [s.layer for s in steps]
        assert "config" in layers


class TestExplainDecorator:
    def test_no_overrides_uses_decorator(self) -> None:
        spec = _spec(
            name="write_file",
            caps=["fs.write"],
            policy=ToolPolicy.ASK_FIRST,
        )
        eff, steps = explain_policy(spec)
        assert eff.policy == ToolPolicy.ASK_FIRST
        last = steps[-1]
        assert last.layer == "decorator"
        assert last.blocking is True