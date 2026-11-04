"""9.8: индикаторы memory / mcp / freedom в StatusDashboard."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from ui.widgets.status_dashboard import StatusDashboard


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class TestMemoryIndicator:
    def test_zero(self, qapp) -> None:
        dash = StatusDashboard()
        dash.set_memory(0, [])
        assert "mem" in dash.memory.text().lower()

    def test_positive(self, qapp) -> None:
        dash = StatusDashboard()
        dash.set_memory(5, ["first", "second", "third"])
        text = dash.memory.text()
        assert "mem: 5" in text
        tooltip = dash.memory.toolTip()
        assert "first" in tooltip
        assert "3" in tooltip or "Рефлексий" in tooltip


class TestMCPIndicator:
    def test_zero(self, qapp) -> None:
        dash = StatusDashboard()
        dash.set_mcp(0, [])
        assert "mcp" in dash.mcp.text().lower()

    def test_positive(self, qapp) -> None:
        dash = StatusDashboard()
        dash.set_mcp(2, ["filesystem", "github"])
        text = dash.mcp.text()
        assert "mcp: 2" in text
        tooltip = dash.mcp.toolTip()
        assert "filesystem" in tooltip
        assert "github" in tooltip


class TestFreedomIndicator:
    @pytest.mark.parametrize(
        ("level", "icon"),
        [
            ("safe", "🔒"),
            ("research", "🔍"),
            ("development", "🛠"),
            ("open", "🌐"),
        ],
    )
    def test_levels(self, qapp, level: str, icon: str) -> None:
        dash = StatusDashboard()
        dash.set_freedom(level)
        assert icon in dash.freedom.text()

    def test_unknown(self, qapp) -> None:
        dash = StatusDashboard()
        dash.set_freedom("unknown")
        assert "profile" in dash.freedom.text().lower()