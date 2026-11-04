"""2.8: ToolService — audit + snapshot + policy."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from agents.ollama_client import ToolCall
from agents.tool_registry import ToolRegistry, ToolResult, tool
from core.audit import AuditLog
from core.config import AgentMode, ToolPolicy
from core.policy import EffectivePolicy
from core.services.tool_service import ToolService


@pytest.fixture
def registry() -> ToolRegistry:
    r = ToolRegistry()

    @tool(name="ok_tool", description="", policy=ToolPolicy.AUTO)
    def ok_tool(x: int = 0) -> str:
        return "ok"

    @tool(
        name="write_tool",
        description="",
        policy=ToolPolicy.ASK_FIRST,
        is_mutating=True,
        capabilities=["fs.write"],
    )
    def write_tool(path: str = "") -> str:
        return f"written:{path}"

    r.register(ok_tool)
    r.register(write_tool)
    return r


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(db_path=tmp_path / "audit.db")


class TestAudit:
    @pytest.mark.asyncio
    async def test_audit_for_every_tool(self, registry, audit) -> None:
        svc = ToolService(registry, audit=audit)
        policy = EffectivePolicy(ToolPolicy.AUTO, True, "default")
        await svc.execute(
            ToolCall(name="ok_tool", arguments={"x": 1}),
            effective_policy=policy,
        )
        assert audit.count() == 1
        entry = audit.get_recent(1)[0]
        assert entry.tool == "ok_tool"
        assert entry.approved is True

    @pytest.mark.asyncio
    async def test_audit_for_error(self, registry, audit) -> None:
        @tool(name="err_tool", description="", policy=ToolPolicy.AUTO)
        def err_tool() -> str:
            raise RuntimeError("boom")

        registry.register(err_tool)
        svc = ToolService(registry, audit=audit)
        policy = EffectivePolicy(ToolPolicy.AUTO, True, "default")
        result = await svc.execute(
            ToolCall(name="err_tool", arguments={}),
            effective_policy=policy,
        )
        assert result.is_error
        assert audit.count() == 1
        assert audit.get_recent(1)[0].error == "error"


class TestPolicyBlocked:
    @pytest.mark.asyncio
    async def test_blocked_does_not_execute(self, registry, audit) -> None:
        svc = ToolService(registry, audit=audit)
        policy = EffectivePolicy(
            ToolPolicy.EXCLUDED, False, "security:secrets"
        )
        result = await svc.execute(
            ToolCall(name="ok_tool", arguments={}),
            effective_policy=policy,
        )
        assert result.is_error
        assert "заблокирован" in result.content
        assert result.metadata is not None
        assert result.metadata["policy_reason"] == "security:secrets"
        assert audit.count() == 1
        assert audit.get_recent(1)[0].approved is False


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_before_mutating(
        self, registry, audit, tmp_path: Path
    ) -> None:
        target = tmp_path / "a.txt"
        target.write_text("old")
        svc = ToolService(
            registry, audit=audit, project_root=str(tmp_path)
        )
        policy = EffectivePolicy(ToolPolicy.ASK_FIRST, True, "profile")
        result = await svc.execute(
            ToolCall(name="write_tool", arguments={"path": "a.txt"}),
            effective_policy=policy,
        )
        assert result.metadata is not None
        assert result.metadata.get("snapshot_id")

    @pytest.mark.asyncio
    async def test_no_snapshot_for_readonly(
        self, registry, audit, tmp_path: Path
    ) -> None:
        svc = ToolService(
            registry, audit=audit, project_root=str(tmp_path)
        )
        policy = EffectivePolicy(ToolPolicy.AUTO, True, "default")
        result = await svc.execute(
            ToolCall(name="ok_tool", arguments={}),
            effective_policy=policy,
        )
        assert result.metadata is None or "snapshot_id" not in (result.metadata or {})