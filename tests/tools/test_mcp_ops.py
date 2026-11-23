"""7: MCPManager — регистрация инструментов + capabilities."""

from __future__ import annotations

from typing import cast

import pytest

from agents.tool_registry import ToolRegistry
from core.config import MCPServerConfig, ToolPolicy
from core.policy import effective_policy
from tools.mcp_ops import MCPManager, _infer_capability


class TestInferCapability:
    def test_write(self) -> None:
        assert _infer_capability("write_file") == "fs.write"

    def test_read(self) -> None:
        assert _infer_capability("read_file") == "fs.read"

    def test_shell(self) -> None:
        assert _infer_capability("run_shell") == "shell"

    def test_net(self) -> None:
        assert _infer_capability("fetch_url") == "net"

    def test_git(self) -> None:
        assert _infer_capability("git_commit") == "git"

    def test_secrets(self) -> None:
        assert _infer_capability("read_token") == "secrets"

    def test_unknown(self) -> None:
        assert _infer_capability("mystery_tool") == "fs.read"


class TestMCPManager:
    @pytest.mark.asyncio
    async def test_register_tools_whitelist(self) -> None:
        """Инструмент с capability вне whitelist → blocked effective_policy."""
        mgr = MCPManager()
        # Заглушка клиента.
        mgr._clients["test"] = cast(  # type: ignore[assignment]
            object(),
            None,  # type: ignore[arg-type]
        )
        from core.mcp_client import MCPToolInfo

        mgr._tools["test"] = [
            MCPToolInfo(
                name="write_file",
                description="write",
                input_schema={"type": "object", "properties": {}},
            ),
            MCPToolInfo(
                name="read_file",
                description="read",
                input_schema={"type": "object", "properties": {}},
            ),
        ]
        mgr._configs["test"] = MCPServerConfig(
            name="test",
            command="python",
            capabilities=["fs.read"],  # только чтение разрешено
            enabled=True,
        )
        mgr._initialized = True

        registry = ToolRegistry()
        n = mgr.register_mcp_tools(registry)
        assert n == 2  # оба зарегистрированы (write будет заблокирован policy)

        # write_file — excluded (capability fs.write не в whitelist).
        spec_write = registry.get_spec("write_file")
        p = effective_policy(spec_write)
        assert p.policy == ToolPolicy.EXCLUDED

        # read_file — разрешён (fs.read в whitelist).
        spec_read = registry.get_spec("read_file")
        p2 = effective_policy(spec_read)
        assert p2.allowed is True
