"""MCP-клиент (7): JSON-RPC 2.0 over stdio.

Один `MCPClient` = один MCP-сервер (subprocess). Транспорт — stdio
(``Content-Length: <n>\\r\\n\\r\\n<json>``), как в LSP.

Жизненный цикл:
  1. ``MCPClient.start()`` — spawn subprocess + ``initialize`` +
     ``notifications/initialized``.
  2. ``list_tools()`` — ``tools/list``.
  3. ``call_tool(name, arguments)`` — ``tools/call``.
  4. ``stop()`` — graceful: ``shutdown``-нотификация (если сервер
     поддерживает) → SIGTERM → SIGKILL.

Менеджер (``MCPManager``) кэширует клиентов по имени сервера и
переиспользует их между вызовами.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

# Таймауты (секунды).
INIT_TIMEOUT = 30.0
CALL_TIMEOUT = 120.0
STOP_TIMEOUT = 5.0

# Версия MCP-протокола, которую мы поддерживаем. Сервер отвечает своей —
# клиент берёт её как «принятую» (MCP позволяет downgrade по требованию
# сервера).
CLIENT_PROTOCOL_VERSION = "2024-11-05"


@dataclass
class MCPToolInfo:
    """Один MCP-инструмент из ``tools/list``."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPToolResult:
    """Результат ``tools/call``."""

    content: str
    is_error: bool = False


class MCPError(Exception):
    """Ошибка MCP-протокола / транспорта."""


class MCPClient:
    """Один MCP-сервер (JSON-RPC 2.0 over stdio)."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        *,
        cwd: str | Path | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args)
        self.env = dict(env or {})
        self.cwd = str(cwd) if cwd else None

        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._initialized = False
        self._lock = asyncio.Lock()
        self.server_info: dict[str, Any] = {}
        self.server_capabilities: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Запускает subprocess и выполняет initialize/initialized."""
        # Собираем env: родительский + дополнительные (MCP_* не наследуем).
        env = {**os.environ, **self.env}
        creationflags = 0
        if os.name == "nt":
            import subprocess as _sp
            creationflags = _sp.CREATE_NO_WINDOW

        try:
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
                creationflags=creationflags,
            )
        except FileNotFoundError as e:
            raise MCPError(
                f"MCP {self.name}: команда не найдена: {self.command} ({e})"
            ) from e

        self._reader_task = asyncio.create_task(self._read_loop())

        try:
            init_result = await asyncio.wait_for(
                self._request(
                    "initialize",
                    {
                        "protocolVersion": CLIENT_PROTOCOL_VERSION,
                        "capabilities": {
                            # Пока никаких клиентских capabilities не
                            # заявляем — нам нужны только tools.
                        },
                        "clientInfo": {
                            "name": "OlliDesk",
                            "version": "0.1.2",
                        },
                    },
                ),
                timeout=INIT_TIMEOUT,
            )
        except Exception:
            await self.stop()
            raise

        self.server_info = init_result.get("serverInfo", {}) or {}
        self.server_capabilities = init_result.get("capabilities", {}) or {}
        await self._notify("notifications/initialized", {})
        self._initialized = True
        logger.info(
            f"MCP {self.name}: connected "
            f"({self.server_info.get('name', '?')} "
            f"{self.server_info.get('version', '?')})"
        )

    async def stop(self) -> None:
        """Graceful shutdown: SIGTERM → SIGKILL."""
        if self.process is not None:
            with contextlib.suppress(Exception):
                self.process.terminate()
                await asyncio.wait_for(
                    self.process.wait(), timeout=STOP_TIMEOUT
                )
        if self.process is not None and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.kill()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        self._initialized = False
        logger.info(f"MCP {self.name}: stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[MCPToolInfo]:
        """Возвращает список инструментов из ``tools/list``."""
        result = await asyncio.wait_for(
            self._request("tools/list", {}), timeout=CALL_TIMEOUT
        )
        raw_tools = result.get("tools") or []
        tools: list[MCPToolInfo] = []
        for t in raw_tools:
            if not isinstance(t, dict):
                continue
            tools.append(
                MCPToolInfo(
                    name=str(t.get("name") or ""),
                    description=str(t.get("description") or ""),
                    input_schema=t.get("inputSchema") or {},
                )
            )
        logger.info(f"MCP {self.name}: {len(tools)} инструментов")
        return tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> MCPToolResult:
        """Вызывает MCP-инструмент."""
        result = await asyncio.wait_for(
            self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            ),
            timeout=CALL_TIMEOUT,
        )
        is_error = bool(result.get("isError", False))
        content_parts = result.get("content") or []
        texts: list[str] = []
        for part in content_parts:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                texts.append(str(part.get("text") or ""))
            elif ptype == "resource":
                res = part.get("resource") or {}
                texts.append(str(res.get("text") or res.get("uri") or ""))
        content = "\n".join(texts).strip() or "(empty result)"
        return MCPToolResult(content=content, is_error=is_error)

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ------------------------------------------------------------------
    # JSON-RPC over stdio
    # ------------------------------------------------------------------

    async def _request(self, method: str, params: dict) -> Any:
        if self.process is None or self.process.stdin is None:
            raise MCPError(f"MCP {self.name}: процесс не запущен")
        req_id = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        await self._write(msg)

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[req_id] = future
        try:
            return await asyncio.wait_for(future, timeout=CALL_TIMEOUT)
        finally:
            self._pending.pop(req_id, None)

    async def _notify(self, method: str, params: dict) -> None:
        if self.process is None or self.process.stdin is None:
            raise MCPError(f"MCP {self.name}: процесс не запущен")
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, msg: dict) -> None:
        assert self.process is not None and self.process.stdin is not None
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self.process.stdin.write(header + body)
        await self.process.stdin.drain()

    async def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = await self.process.stdout.readline()
                    if not line:
                        logger.debug(f"MCP {self.name}: EOF в reader")
                        return
                    s = line.decode("ascii", errors="replace").strip()
                    if not s:
                        break
                    if ":" in s:
                        k, v = s.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                length_str = headers.get("content-length", "0")
                try:
                    length = int(length_str)
                except ValueError:
                    continue
                if length <= 0:
                    continue
                body = await self.process.stdout.readexactly(length)
                try:
                    msg = json.loads(body)
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"MCP {self.name}: битый JSON ({e}): {body[:200]!r}"
                    )
                    continue
                self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"MCP {self.name}: reader упал: {e}")

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg:
            future = self._pending.get(int(msg["id"]))
            if future is not None and not future.done():
                if "error" in msg:
                    err = msg["error"]
                    future.set_exception(
                        MCPError(
                            f"MCP {self.name}: {err.get('message', err)}"
                        )
                    )
                else:
                    future.set_result(msg.get("result"))
            return
        # Нотификации от сервера (``notifications/*``) — логируем и
        # игнорируем. ``publishDiagnostics``-аналога у MCP нет в 2024-11.
        method = msg.get("method")
        if method:
            logger.debug(f"MCP {self.name}: notification {method}")