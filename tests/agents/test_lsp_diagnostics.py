"""10.8: LSP-диагностика (mock LSP)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents.lsp_bridge import LspBridge, _language_for_path


class TestLanguageForPath:
    def test_python(self):
        assert _language_for_path(Path("a.py")) == "python"

    def test_csharp(self):
        assert _language_for_path(Path("a.cs")) == "csharp"

    def test_unknown(self):
        assert _language_for_path(Path("a.xyz")) is None


class TestDiagnostics:
    @pytest.mark.asyncio
    async def test_unavailable_returns_empty(self, tmp_path: Path) -> None:
        from core.security import SecurityPolicy

        bridge = LspBridge(tmp_path / "cache", SecurityPolicy())
        bridge.available = MagicMock(return_value=False)
        p = tmp_path / "a.py"
        p.write_text("x = 1")
        result = await bridge.diagnostics_async(p)
        assert result == []

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        from core.security import SecurityPolicy

        bridge = LspBridge(tmp_path / "cache", SecurityPolicy())
        result = await bridge.diagnostics_async(tmp_path / "missing.py")
        assert result == []
