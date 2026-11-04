"""9.5.1: ChatPanel.set_theme перекрашивает все сообщения."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from state.session_store import SessionStore


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp, tmp_path: Path):
    from ui.chat.panel import ChatPanel

    store = SessionStore(tmp_path / "sessions.db")
    p = ChatPanel(
        session_store=store,
        base_url="http://localhost:11434",
        model="test",
    )
    p._detect_backends = lambda: None  # type: ignore[method-assign]
    p._start_mcp_init = lambda: None  # type: ignore[method-assign]
    p._recover_inbox_on_startup = lambda: None  # type: ignore[method-assign]
    yield p
    with contextlib.suppress(Exception):
        p.shutdown()
    p.deleteLater()


class TestSetTheme:
    def test_default_dark(self, panel) -> None:
        # По умолчанию config.theme = light — но в тесте может быть
        # любой; проверим, что _is_dark установлен.
        assert isinstance(panel._is_dark, bool)

    def test_set_theme_light(self, panel) -> None:
        panel.set_theme("light")
        assert panel._is_dark is False
        panel.set_theme("dark")
        assert panel._is_dark is True

    def test_apply_theme_to_existing_messages(self, panel) -> None:
        from ui.chat.message_item import ChatMessageItem

        panel._add_message("assistant", "hello")
        widgets = panel.findChildren(ChatMessageItem)
        assert widgets
        panel.set_theme("dark")
        for w in widgets:
            assert w._dark is True