"""LSP-мост: definition/references/hover поверх tree-sitter-графа.

Полноценный call-graph НЕ гоняем на весь проект по умолчанию (дорого).
LSP включается точечно для bug_analysis/refactor («где вызывается X»).

Реализация — минимальный JSON-RPC 2.0 клиент через asyncio.subprocess.
НЕ требует pygls в runtime (pygls — опциональная зависимость для
серверной стороны; клиентская часть здесь самодостаточна).

Жизненный цикл:
  1. `ensure_installed(language)` — ставит сервер, если разрешено политикой;
  2. `_get_client(language, project_root)` — лениво запускает процесс;
  3. `definition/references` — шлют textDocument/{definition,references};
  4. `stop_all()` — завершает все серверы (graceful shutdown → SIGTERM).

Позиции: LSP использует **0-based** line/character. Вызывающий код обязан
передавать именно их (не 1-based из UI).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from core.security import SecurityPolicy, assert_outbound_allowed


@dataclass
class LspServerSpec:
    language: str
    cmd_install: list[str]
    cmd_serve: list[str]
    size_mb_est: int
    allow_offline_bundle: bool = True


INSTALLERS: dict[str, LspServerSpec] = {
    "python": LspServerSpec(
        "python",
        ["python", "-m", "pip", "install", "pylsp"],
        ["pylsp"],
        size_mb_est=15,
    ),
    "csharp": LspServerSpec(
        "csharp",
        ["dotnet", "tool", "install", "-g", "roslyn-lsp"],
        ["roslyn-lsp"],
        size_mb_est=80,
    ),
    "ruby": LspServerSpec(
        "ruby",
        ["gem", "install", "solargraph"],
        ["solargraph", "stdio"],
        size_mb_est=40,
    ),
    "go": LspServerSpec(
        "go",
        ["go", "install", "golang.org/x/tools/gopls@latest"],
        ["gopls"],
        size_mb_est=30,
    ),
    "terraform": LspServerSpec(
        "terraform",
        ["wget", "-q", "-O", "terraform-ls",
         "https://github.com/hashicorp/terraform-ls/releases/latest/download/terraform-ls"],
        ["terraform-ls", "serve"],
        size_mb_est=25,
    ),
}


class _LspClient:
    """Один запущенный LSP-сервер (JSON-RPC 2.0 over stdio)."""

    # Таймаут на один запрос (definition/references обычно отвечают <1 с;
    # первая инициализация может занять больше — отдельно).
    REQUEST_TIMEOUT = 15.0
    INIT_TIMEOUT = 30.0

    def __init__(self, cmd: list[str], cwd: str) -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._initialized = False
        self._opened_files: set[str] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Запускает процесс и выполняет initialize/initialized."""
        self.process = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

        try:
            await asyncio.wait_for(
                self._request(
                    "initialize",
                    {
                        "processId": os.getpid(),
                        "rootUri": Path(self.cwd).as_uri(),
                        "capabilities": {
                            "textDocument": {
                                "definition": {"linkSupport": False},
                                "references": {},
                            }
                        },
                    },
                ),
                timeout=self.INIT_TIMEOUT,
            )
            await self._notify("initialized", {})
            self._initialized = True
            logger.info(f"LSP client started: {' '.join(self.cmd)}")
        except Exception:
            await self.stop()
            raise

    async def _request(self, method: str, params: dict) -> Any:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("LSP process not started")
        req_id = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        await self._write(msg)

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[req_id] = future
        try:
            return await asyncio.wait_for(future, timeout=self.REQUEST_TIMEOUT)
        finally:
            self._pending.pop(req_id, None)

    async def _notify(self, method: str, params: dict) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("LSP process not started")
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, msg: dict) -> None:
        assert self.process is not None and self.process.stdin is not None
        body = json.dumps(msg).encode("utf-8")
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
                        return
                    line_str = line.decode("ascii", errors="replace").strip()
                    if not line_str:
                        break
                    if ":" in line_str:
                        k, v = line_str.split(":", 1)
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
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"LSP reader loop failed: {e}")

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg:
            future = self._pending.get(msg["id"])
            if future is not None and not future.done():
                if "error" in msg:
                    err = msg["error"]
                    future.set_exception(
                        RuntimeError(err.get("message", f"LSP error {err}"))
                    )
                else:
                    future.set_result(msg.get("result"))

    async def ensure_open(self, file_path: str, language_id: str) -> None:
        """Шлёт textDocument/didOpen если файл ещё не открыт (один раз)."""
        async with self._lock:
            if file_path in self._opened_files:
                return
            try:
                text = Path(file_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return
            await self._notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": Path(file_path).as_uri(),
                        "languageId": language_id,
                        "version": 1,
                        "text": text,
                    }
                },
            )
            self._opened_files.add(file_path)

    async def definition(
        self, file_path: str, line: int, col: int, language_id: str
    ) -> dict | None:
        """textDocument/definition. line/col — 0-based."""
        await self.ensure_open(file_path, language_id)
        result = await self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": Path(file_path).as_uri()},
                "position": {"line": line, "character": col},
            },
        )
        if not result:
            return None
        if isinstance(result, list):
            return result[0] if result else None
        return result if isinstance(result, dict) else None

    async def references(
        self, file_path: str, line: int, col: int, language_id: str
    ) -> list[dict]:
        """textDocument/references. line/col — 0-based."""
        await self.ensure_open(file_path, language_id)
        result = await self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": Path(file_path).as_uri()},
                "position": {"line": line, "character": col},
                "context": {"includeDeclaration": True},
            },
        )
        if not isinstance(result, list):
            return []
        return [r for r in result if isinstance(r, dict)]

    async def stop(self) -> None:
        """Graceful shutdown → SIGTERM → SIGKILL."""
        if self._initialized and self.process is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._request("shutdown", {}), timeout=3.0)
                await self._notify("exit", {})
        if self.process is not None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self._initialized = False


class LspBridge:
    """Высокоуровневый мост: кэширует клиентов по языку, деградирует gracefully."""

    def __init__(self, cache_dir: Path, policy: SecurityPolicy) -> None:
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self._clients: dict[str, _LspClient] = {}
        self._lock = asyncio.Lock()

    def available(self, language: str) -> bool:
        """True, если бинарь сервера уже в PATH."""
        spec = INSTALLERS.get(language)
        if not spec:
            return False
        return shutil.which(spec.cmd_serve[0]) is not None

    def ensure_installed(
        self, language: str, *, interactive_confirm: bool = False
    ) -> bool:
        """Ставит сервер, если разрешено политикой (см. §7 плана)."""
        spec = INSTALLERS.get(language)
        if spec is None:
            return False
        if self.available(language):
            return True
        if not self.policy.allow_lsp_download:
            logger.info(f"LSP {language}: установка запрещена политикой")
            return False

        big = spec.size_mb_est >= self.policy.lsp_download_size_mb_warn
        if big and not interactive_confirm:
            logger.warning(
                f"LSP {language}: сервер ~{spec.size_mb_est} МБ. "
                "Автоустановка отключена — требуется interactive_confirm=True "
                "(UI-хук с вопросом пользователю)."
            )
            return False

        for tok in spec.cmd_install:
            if tok.startswith("http"):
                try:
                    assert_outbound_allowed(tok)
                except PermissionError as e:
                    logger.error(f"LSP {language}: {e}")
                    return False

        env = {**os.environ, "PIP_TARGET": str(self.cache / "py")}
        try:
            subprocess.run(
                spec.cmd_install, check=True, timeout=300, env=env,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )
        except Exception as e:
            logger.error(f"LSP {language}: установка не удалась: {e}")
            return False
        return self.available(language)

    async def _get_client(
        self, language: str, project_root: str | None
    ) -> _LspClient | None:
        if not self.available(language):
            return None
        cached = self._clients.get(language)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._clients.get(language)
            if cached is not None:
                return cached
            spec = INSTALLERS[language]
            cwd = project_root or str(self.cache)
            client = _LspClient(spec.cmd_serve, cwd)
            try:
                await client.start()
            except Exception as e:
                logger.warning(f"LSP {language}: не удалось запустить: {e}")
                return None
            self._clients[language] = client
            return client

    async def references(
        self,
        language: str,
        file: str,
        line: int,
        col: int,
        *,
        project_root: str | None = None,
    ) -> list[dict]:
        """textDocument/references (0-based позиции). Пустой список при сбое."""
        client = await self._get_client(language, project_root)
        if client is None:
            return []
        try:
            return await client.references(file, line, col, language)
        except Exception as e:
            logger.warning(f"LSP references {language}: {e}")
            return []

    async def definition(
        self,
        language: str,
        file: str,
        line: int,
        col: int,
        *,
        project_root: str | None = None,
    ) -> dict | None:
        """textDocument/definition (0-based). None при сбое."""
        client = await self._get_client(language, project_root)
        if client is None:
            return None
        try:
            return await client.definition(file, line, col, language)
        except Exception as e:
            logger.warning(f"LSP definition {language}: {e}")
            return None

    async def stop_all(self) -> None:
        """Завершает все запущенные серверы."""
        for client in list(self._clients.values()):
            with contextlib.suppress(Exception):
                await client.stop()
        self._clients.clear()