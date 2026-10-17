"""LSP-мост (optional-enhancement): definition/references/hover поверх графа.

Полноценный call-graph НЕ гоняем на весь проект по умолчанию (дорого). LSP
включается точечно для bug_analysis/refactor («где вызывается X»). Установка
серверов — через allowlist (pypi/nuget/rubygems), с порогом «большой объём»:
маленькие ставим молча в кэш, большие — в фон/с спросом. Офлайн-альтернатива —
бандлинг в установщик (решение владельца). Локальность данных проекта не
нарушается: LSP работает только с локальными файлами, сеть — лишь для скачивания
самого сервера (dev-инструмент, не контент проекта).
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.security import SecurityPolicy, assert_outbound_allowed


@dataclass
class LspServerSpec:
    language: str
    cmd_install: list[str]      # команда установки (pip/nuget/gem)
    cmd_serve: list[str]        # команда запуска сервера
    size_mb_est: int            # оценка объёма для порога
    allow_offline_bundle: bool = True


INSTALLERS: dict[str, LspServerSpec] = {
    "python": LspServerSpec("python", ["python", "-m", "pip", "install", "pylsp"],
                            ["pylsp"], size_mb_est=15),
    "csharp": LspServerSpec("csharp", ["dotnet", "tool", "install", "-g", "roslyn-lsp"],
                            ["roslyn-lsp"], size_mb_est=80),   # «большой» → фон/спрос
    "ruby":   LspServerSpec("ruby", ["gem", "install", "solargraph"],
                            ["solargraph", "stdio"], size_mb_est=40),
    "go":     LspServerSpec("go", ["go", "install", "golang.org/x/tools/gopls@latest"],
                            ["gopls"], size_mb_est=30),
    "terraform": LspServerSpec("terraform", ["wget", "-q", "https://github.com/hashicorp/terraform-ls/releases/latest/download/terraform-ls"],
                            ["terraform-ls", "serve"], size_mb_est=25),
}


class LspBridge:
    def __init__(self, cache_dir: Path, policy: SecurityPolicy) -> None:
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self._running: dict[str, subprocess.Popen] = {}

    def available(self, language: str) -> bool:
        spec = INSTALLERS.get(language)
        if not spec:
            return False
        exe = shutil.which(spec.cmd_serve[0])
        return exe is not None

    def ensure_installed(self, language: str, *, interactive_confirm: bool = False) -> bool:
        """Ставит сервер если нужно. Порог «большой объём» => спрос/фон."""
        spec = INSTALLERS.get(language)
        if not spec or self.available(language):
            return self.available(language)
        if not self.policy.allow_lsp_download:
            return False
        big = spec.size_mb_est >= self.policy.lsp_download_size_mb_warn
        if big and interactive_confirm:
            # UI-хук: показать «скачать ~{size} МБ?» — решает владелец.
            pass
        # allowlist-гард для сетевых установщиков (wget/nuget/go).
        for tok in spec.cmd_install:
            if tok.startswith("http"):
                assert_outbound_allowed(tok)
        try:
            subprocess.run(spec.cmd_install, check=True, timeout=300,
                           env={**__import__("os").environ, "PIP_TARGET": str(self.cache / "py")})
            return self.available(language)
        except Exception:
            return False

    async def references(self, language: str, file: str, line: int, col: int) -> list[dict]:
        """Find References через запущенный сервер (best-effort, таймаут)."""
        if not self.available(language):
            return []
        # Реальная реализация — pygls-клиент с initialize/textDocument/references.
        # Здесь — стабильный интерфейс; тело зависит от выбранного транспорта.
        raise NotImplementedError("подключи pygls-клиент в этой точке")

    async def definition(self, language: str, file: str, line: int, col: int) -> dict | None:
        if not self.available(language):
            return None
        raise NotImplementedError("подключи pygls-клиент в этой точке")