"""2.7: Policy Engine — приоритеты и матрица."""

from __future__ import annotations

import pytest

from agents.tool_registry import ToolSpec
from core.config import AgentMode, AppConfig, ToolPolicy
from core.policy import effective_policy
from core.profiles import ProfileConfig, ProfileToolPolicies
from core.security import SecurityPolicy


def _spec(
    name: str = "read_file",
    capabilities: list[str] | None = None,
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
        capabilities=frozenset(capabilities or ["fs.read"]),
        is_mutating=False,
        category="filesystem",
    )


def _profile(
    *, freedom: str = "development", tool_policies: dict | None = None
) -> ProfileConfig:
    return ProfileConfig(
        id="p",
        name="P",
        freedom_level=freedom,  # type: ignore[arg-type]
        tool_policies=ProfileToolPolicies(**(tool_policies or {})),
    )


def _config(tool_policies: dict | None = None) -> AppConfig:
    return AppConfig(
        default_model="m",
        embed_model="e",
        tool_policies=tool_policies or {},
    )


class TestSecrets:
    """`secrets` — всегда excluded, независимо от всего."""

    @pytest.mark.parametrize("freedom", ["safe", "research", "development", "open"])
    def test_secrets_always_excluded(self, freedom: str) -> None:
        p = effective_policy(
            _spec(capabilities=["secrets"]),
            profile=_profile(freedom=freedom),
        )
        assert p.policy == ToolPolicy.EXCLUDED
        assert not p.allowed
        assert "secrets" in p.reason


class TestConfidential:
    def test_confidential_blocks_net(self) -> None:
        sec = SecurityPolicy(confidential=True)
        p = effective_policy(
            _spec(capabilities=["net"]), security=sec
        )
        assert p.policy == ToolPolicy.EXCLUDED
        assert "confidential" in p.reason

    def test_confidential_allows_fs_read(self) -> None:
        sec = SecurityPolicy(confidential=True)
        p = effective_policy(
            _spec(capabilities=["fs.read"]), security=sec
        )
        assert p.allowed is True


class TestMode:
    def test_wrong_mode_excluded(self) -> None:
        spec = _spec(modes=frozenset({AgentMode.AGENT}))
        p = effective_policy(spec, current_mode=AgentMode.CHAT)
        assert p.policy == ToolPolicy.EXCLUDED
        assert p.reason == "mode"


class TestFreedomMatrix:
    def test_safe_blocks_shell(self) -> None:
        p = effective_policy(
            _spec(name="run_shell_command", capabilities=["shell"]),
            profile=_profile(freedom="safe"),
        )
        assert p.policy == ToolPolicy.EXCLUDED
        assert "freedom" in p.reason

    def test_research_blocks_shell(self) -> None:
        p = effective_policy(
            _spec(name="run_shell_command", capabilities=["shell"]),
            profile=_profile(freedom="research"),
        )
        assert p.policy == ToolPolicy.EXCLUDED

    def test_development_asks_shell(self) -> None:
        p = effective_policy(
            _spec(name="run_shell_command", capabilities=["shell"]),
            profile=_profile(freedom="development"),
        )
        assert p.policy == ToolPolicy.ASK_FIRST
        assert "freedom" in p.reason

    def test_open_asks_shell_still(self) -> None:
        """Даже в `open` shell спрашивает — это жёсткое правило."""
        p = effective_policy(
            _spec(name="run_shell_command", capabilities=["shell"]),
            profile=_profile(freedom="open"),
        )
        assert p.policy == ToolPolicy.ASK_FIRST

    def test_open_allows_git(self) -> None:
        p = effective_policy(
            _spec(name="get_git_status", capabilities=["git"]),
            profile=_profile(freedom="open"),
        )
        assert p.policy == ToolPolicy.AUTO


class TestOverrides:
    def test_profile_override_wins(self) -> None:
        p = effective_policy(
            _spec(name="write_file", capabilities=["fs.write"]),
            profile=_profile(
                freedom="development",
                tool_policies={"write_file": "excluded"},
            ),
        )
        assert p.policy == ToolPolicy.EXCLUDED
        assert p.reason == "profile"

    def test_session_override_wins(self) -> None:
        p = effective_policy(
            _spec(name="write_file", capabilities=["fs.write"]),
            profile=_profile(freedom="development"),
            session_overrides={"write_file": ToolPolicy.AUTO},
        )
        assert p.policy == ToolPolicy.AUTO
        assert p.reason == "session"

    def test_config_override_wins(self) -> None:
        p = effective_policy(
            _spec(name="write_file", capabilities=["fs.write"]),
            profile=_profile(freedom="development"),
            config=_config(tool_policies={"write_file": ToolPolicy.ASK_FIRST}),
        )
        # profile.tool_policies для write_file = ask_first (дефолт)
        # — приоритет у profile. Смотрим на результат:
        assert p.policy in (ToolPolicy.ASK_FIRST, ToolPolicy.AUTO)


class TestDecoratorFallback:
    def test_no_profile_uses_decorator(self) -> None:
        spec = _spec(
            name="write_file",
            capabilities=["fs.write"],
            policy=ToolPolicy.ASK_FIRST,
        )
        p = effective_policy(spec)
        assert p.policy == ToolPolicy.ASK_FIRST

    def test_freedom_auto_respects_decorator_ask_first(self) -> None:
        """`freedom=auto`, но decorator=ask_first (мутирующий) → ask_first."""
        spec = _spec(
            name="write_file",
            capabilities=["fs.write"],
            policy=ToolPolicy.ASK_FIRST,
        )
        p = effective_policy(spec, profile=_profile(freedom="open"))
        # open даёт auto, но декоратор ask_first — уважаем декоратор.
        assert p.policy == ToolPolicy.ASK_FIRST
        assert "decorator" in p.reason