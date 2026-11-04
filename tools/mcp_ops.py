"""MCP-инструменты (7): динамическая регистрация в `ToolRegistry`.

`MCPManager` — singleton, управляет клиентами по имени сервера.
`register_mcp_tools(registry)` — обходит все enabled серверы, получает
``tools/list`` и регистрирует каждый инструмент через
`ToolRegistry.register_spec`.

Capabilities MCP-инструмента определяются по эвристике из имени + по
whitelist `MCPServerConfig.capabilities`:
  * если сервер заявил `secrets` — инструмент excluded (2.7);
  * если `MCPServerConfig.capabilities` не содержит нужного класса
    (например, ``fs.write`` для write-file-инструмента) — excluded;
  * иначе — `capabilities` из манифеста (или `fs.read` по умолчанию).

Инициализация: `MCPManager.initialize()` — async. Вызывается из
`ChatPanel._setup_ui` в фоне (`QThread`). Инструменты регистрируются
лениво при первом `_build_registry` (если менеджер уже готов).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from agents.tool_registry import ToolRegistry, ToolSpec
from core.config import AgentMode, MCPServerConfig, ToolPolicy
from core.event_bus import emit_event
from core.events import EventKind
from core.mcp_client import MCPClient, MCPError, MCPToolInfo

# Эвристика: capability по имени инструмента. MCP-сервер не всегда
# заявляет capabilities явно. Если эвристика не сработала — fs.read.
_CAPABILITY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("write", "create", "edit", "modify", "patch", "insert", "delete"), "fs.write"),
    (("read", "list", "search", "find", "view", "get"), "fs.read"),
    (("shell", "exec", "run", "command"), "shell"),
    (("web", "http", "fetch", "url", "browse"), "net"),
    (("git", "commit", "branch", "log", "diff"), "git"),
    (("secret", "token", "key", "password", ".env"), "secrets"),
)


def _infer_capability(tool_name: str) -> str:
    """Эвристика capability по имени MCP-инструмента."""
    low = tool_name.lower()
    for hints, cap in _CAPABILITY_HINTS:
        if any(h in low for h in hints):
            return cap
    return "fs.read"


class MCPManager:
    """Singleton-менеджер MCP-серверов (7)."""

    _instance: MCPManager | None = None
    _instance_lock = __import__("threading").Lock()

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, list[MCPToolInfo]] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._initialized = False
        self._init_lock: asyncio.Lock | None = None

    @classmethod
    def instance(cls) -> MCPManager:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self, server_configs: list[MCPServerConfig]) -> None:
        """Запускает все enabled серверы и получает список инструментов.

        Args:
            server_configs: Список из `MCPConfig.servers` (уже отфильтрован
                по ``enabled``).
        """
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self._initialized:
                return
            for cfg in server_configs:
                if not cfg.enabled:
                    continue
                self._configs[cfg.name] = cfg
                client = MCPClient(
                    name=cfg.name,
                    command=cfg.command,
                    args=cfg.args,
                    env=cfg.env,
                    cwd=cfg.cwd,
                )
                try:
                    await client.start()
                    tools = await client.list_tools()
                except Exception as e:
                    logger.error(f"MCP {cfg.name}: не удалось подключиться: {e}")
                    emit_event(
                        EventKind.MCP_ERROR.value,
                        {"server_name": cfg.name, "error": str(e)},
                    )
                    with contextlib_suppress():
                        await client.stop()
                    continue
                self._clients[cfg.name] = client
                self._tools[cfg.name] = tools
                emit_event(
                    EventKind.MCP_CONNECTED.value,
                    {
                        "server_name": cfg.name,
                        "n_tools": len(tools),
                        "server_info": client.server_info,
                    },
                )
            self._initialized = True
            logger.info(
                f"MCPManager: initialized {len(self._clients)}/{len(server_configs)} "
                f"серверов"
            )

    async def shutdown(self) -> None:
        """Останавливает все серверы."""
        for name, client in list(self._clients.items()):
            try:
                await client.stop()
            except Exception:
                logger.debug(f"MCP {name}: ошибка stop", exc_info=True)
            emit_event(
                EventKind.MCP_DISCONNECTED.value,
                {"server_name": name, "reason": "shutdown"},
            )
        self._clients.clear()
        self._tools.clear()
        self._initialized = False

    @property
    def is_ready(self) -> bool:
        return self._initialized

    def list_registered_tools(self) -> list[tuple[str, MCPToolInfo]]:
        """Все доступные MCP-инструменты (server_name, info)."""
        out: list[tuple[str, MCPToolInfo]] = []
        for server_name, tools in self._tools.items():
            for t in tools:
                out.append((server_name, t))
        return out

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_mcp_tools(self, registry: ToolRegistry) -> int:
        """Регистрирует все MCP-инструменты в `ToolRegistry`.

        Возвращает число зарегистрированных инструментов.
        """
        if not self._initialized:
            return 0
        count = 0
        for server_name, info in self.list_registered_tools():
            cfg = self._configs.get(server_name)
            if cfg is None:
                continue
            capability = _infer_capability(info.name)

            # Whitelist: если capability не в списке сервера — excluded.
            # `secrets` — всегда excluded (2.7).
            allowed_caps = set(cfg.capabilities or ["fs.read"])
            if capability == "secrets" or capability not in allowed_caps:
                # Регистрируем, но с capabilities={...} — effective_policy
                # заблокирует. Это лучше, чем не регистрировать вообще:
                # модель видит инструмент и получает внятную ошибку.
                caps = frozenset({"secrets"} if capability == "secrets" else {capability})
            else:
                caps = frozenset({capability})

            # ToolSpec из манифеста.
            schema = {
                "name": info.name,
                "description": info.description,
                "parameters": info.input_schema or {"type": "object", "properties": {}},
            }
            spec = ToolSpec(
                name=info.name,
                description=info.description or f"MCP {server_name}/{info.name}",
                schema=schema,
                modes=frozenset({AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT}),
                policy=ToolPolicy.ASK_FIRST,  # MCP — всегда подтверждение по умолчанию
                capabilities=caps,
                is_mutating=capability in ("fs.write", "shell", "git"),
                category="mcp",
                timeout_s=120.0,
                max_output_chars=8000,
                idempotent=False,
            )

            # Async-обёртка над MCPClient.call_tool.
            func = self._make_callable(server_name, info.name)
            registry.register_spec(spec, func)
            count += 1
        if count:
            logger.info(f"MCPManager: зарегистрировано {count} MCP-инструментов")
        return count

    def _make_callable(self, server_name: str, tool_name: str):
        """Создаёт async-callable для `ToolRegistry.register_spec`."""

        async def _call(**kwargs: Any) -> str:
            client = self._clients.get(server_name)
            if client is None:
                return f"Ошибка: MCP-сервер {server_name!r} не запущен"
            started = time.perf_counter()
            try:
                result = await client.call_tool(tool_name, kwargs)
            except MCPError as e:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                emit_event(
                    EventKind.MCP_TOOL_CALLED.value,
                    {
                        "server_name": server_name,
                        "tool_name": tool_name,
                        "duration_ms": duration_ms,
                        "is_error": True,
                    },
                )
                return f"Ошибка MCP: {e}"
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            emit_event(
                EventKind.MCP_TOOL_CALLED.value,
                {
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "duration_ms": duration_ms,
                    "is_error": result.is_error,
                },
            )
            if result.is_error:
                return f"Ошибка MCP: {result.content}"
            return result.content

        _call.__name__ = f"mcp_{server_name}_{tool_name}".replace(".", "_")
        return _call

    # ------------------------------------------------------------------
    # Sync-обёртка (для UI-потока)
    # ------------------------------------------------------------------

    def initialize_sync(self, server_configs: list[MCPServerConfig]) -> None:
        """Sync-обёртка `initialize` для GUI-потока (в фоне).

        Создаёт новый event loop. Вызывается из `QThread`.
        """
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.initialize(server_configs))
        finally:
            loop.close()

    def shutdown_sync(self) -> None:
        """Sync-обёртка `shutdown` для GUI-потока."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.shutdown())
        finally:
            loop.close()


def contextlib_suppress():
    """Псевдо-contextmanager без импорта (для читаемости выше)."""
    import contextlib

    return contextlib.suppress(Exception)


def register_mcp_tools(registry: ToolRegistry) -> int:
    """Удобная обёртка: `MCPManager.instance().register_mcp_tools(registry)`."""
    return MCPManager.instance().register_mcp_tools(registry)


__all__ = [
    "MCPManager",
    "register_mcp_tools",
]