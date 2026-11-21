"""9.10: PermissionsDashboard — 4 вкладки, session overrides."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from core.config import AgentMode, AppConfig, ToolPolicy
from core.profiles import ProfileConfig, ProfileToolPolicies


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _make_registry():
    from agents.tool_registry import ToolRegistry, tool

    @tool(name="read_file", description="", policy=ToolPolicy.AUTO)
    def read_file(path: str = "") -> str:
        return ""

    @tool(
        name="write_file",
        description="",
        policy=ToolPolicy.ASK_FIRST,
        capabilities=["fs.write"],
    )
    def write_file(path: str = "", content: str = "") -> str:
        return ""

    @tool(
        name="run_shell_command",
        description="",
        policy=ToolPolicy.ASK_FIRST,
        capabilities=["shell"],
    )
    def run_shell_command(command: str = "") -> str:
        return ""

    r = ToolRegistry()
    r.register(read_file)
    r.register(write_file)
    r.register(run_shell_command)
    return r


@pytest.fixture
def dashboard(qapp):
    from ui.dialogs.permissions_dashboard import PermissionsDashboard

    cfg = AppConfig(default_model="m", embed_model="e")
    profile = ProfileConfig(
        id="development",
        name="Разработка",
        freedom_level="development",
        tool_policies=ProfileToolPolicies(),
    )
    dlg = PermissionsDashboard(
        _make_registry(),
        cfg,
        active_profile=profile,
        current_mode=AgentMode.AGENT,
        session_overrides={},
    )
    yield dlg
    dlg.close()
    dlg.deleteLater()


class TestMatrix:
    def test_tab_present(self, dashboard) -> None:
        assert dashboard._tabs.count() == 4
        assert dashboard._tabs.tabText(0) == "По профилям"

    def test_matrix_has_rows(self, dashboard) -> None:
        assert dashboard._matrix.rowCount() == 3  # 3 инструмента

    def test_matrix_columns_match_profiles(self, dashboard) -> None:
        # 2 фиксированных столбца + профили.
        assert dashboard._matrix.columnCount() >= 3

    def test_matrix_colors(self, dashboard) -> None:
        # read_file: auto → зелёный в колонке development.
        # Найдём строку read_file.
        for row in range(dashboard._matrix.rowCount()):
            if dashboard._matrix.item(row, 0).text() == "read_file":
                cell = dashboard._matrix.item(row, 2)
                assert cell is not None
                assert cell.text() == "auto"
                break


class TestCapabilities:
    def test_tab_present(self, dashboard) -> None:
        assert dashboard._tabs.tabText(1) == "По capabilities"


class TestAudit:
    def test_tab_present(self, dashboard) -> None:
        assert dashboard._tabs.tabText(2) == "Последние вызовы"


class TestExplain:
    def test_tab_present(self, dashboard) -> None:
        assert dashboard._tabs.tabText(3) == "Почему заблокировано"

    def test_combo_has_tools(self, dashboard) -> None:
        assert dashboard._explain_combo.count() == 3

    def test_explain_shows_chain(self, dashboard) -> None:
        dashboard._explain_combo.setCurrentIndex(0)
        dashboard._on_explain_changed(0)
        html = dashboard._explain_view.toHtml()
        # Должны быть шаги цепочки.
        for layer in ("secrets", "security", "mode", "freedom"):
            assert layer in html


class TestSessionOverrides:
    def test_shows_empty(self, dashboard) -> None:
        assert "Session overrides: —" in dashboard._overrides_label.text()
        assert not dashboard._reset_overrides_btn.isEnabled()

    def test_reset_clears(self, dashboard) -> None:
        # Добавляем override и сбрасываем.
        dashboard._session_overrides = {"read_file": ToolPolicy.AUTO}
        dashboard._refresh_overrides_label()
        assert dashboard._reset_overrides_btn.isEnabled()
        dashboard._on_reset_overrides()
        assert not dashboard._session_overrides
        assert not dashboard._reset_overrides_btn.isEnabled()
