"""7: MCPClient — JSON-RPC over stdio (mock-сервер)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.mcp_client import (
    MCPClient,
    MCPError,
    MCPToolResult,
)

# ---------------------------------------------------------------------------
# Mock MCP-сервер (крошечный python-скрипт, отвечающий на MCP-запросы).
# ---------------------------------------------------------------------------


_MOCK_SERVER = r'''
import json, sys

def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        s = line.decode("ascii", errors="replace").strip()
        if not s:
            break
        k, v = s.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    body = sys.stdin.buffer.read(n)
    return json.loads(body)

def write_msg(obj):
    body = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()

while True:
    msg = read_msg()
    if msg is None:
        break
    method = msg.get("method")
    if method == "initialize":
        write_msg({"jsonrpc": "2.0", "id": msg["id"],
                   "result": {"protocolVersion": "2024-11-05",
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "mock", "version": "0.1"}}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
            {"name": "echo", "description": "echo back",
             "inputSchema": {"type": "object",
                             "properties": {"text": {"type": "string"}},
                             "required": ["text"]}}
        ]}})
    elif method == "tools/call":
        args = msg["params"].get("arguments") or {}
        text = args.get("text", "")
        write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }})
    else:
        write_msg({"jsonrpc": "2.0", "id": msg["id"],
                   "error": {"code": -32601, "message": f"unknown {method}"}})
'''


@pytest.fixture
def mock_server(tmp_path: Path) -> Path:
    script = tmp_path / "mock_mcp.py"
    script.write_text(_MOCK_SERVER, encoding="utf-8")
    return script


class TestMCPClient:
    @pytest.mark.asyncio
    async def test_initialize_and_list(
        self, mock_server: Path, tmp_path: Path
    ) -> None:
        import sys

        client = MCPClient(
            name="mock",
            command=sys.executable,
            args=[str(mock_server)],
        )
        try:
            await client.start()
            assert client.initialized
            assert client.server_info.get("name") == "mock"
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "echo"
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_call_tool(
        self, mock_server: Path, tmp_path: Path
    ) -> None:
        import sys

        client = MCPClient(
            name="mock",
            command=sys.executable,
            args=[str(mock_server)],
        )
        try:
            await client.start()
            result = await client.call_tool("echo", {"text": "hello"})
            assert isinstance(result, MCPToolResult)
            assert result.content == "hello"
            assert result.is_error is False
        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_missing_command_raises(self) -> None:
        client = MCPClient(
            name="missing",
            command="__nonexistent_command__",
            args=[],
        )
        with pytest.raises(MCPError, match="команда не найдена"):
            await client.start()