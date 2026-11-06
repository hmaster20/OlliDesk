"""9.1: EditorWidget.tabs_changed — эмиты на всех путях."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from ui.web_editor.editor_widget import EditorWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(qapp, tmp_path: Path):
    widget = EditorWidget()
    widget.set_file_reader(lambda p: Path(p).read_text(encoding="utf-8"))
    widget.set_file_writer(lambda p, c: True)
    yield widget
    widget.deleteLater()


def _counts(editor: EditorWidget) -> list[int]:
    received: list[int] = []
    editor.tabs_changed.connect(received.append)
    return received


class TestTabsChanged:
    def test_open_emits(self, editor: EditorWidget, tmp_path: Path) -> None:
        counts = _counts(editor)
        p = tmp_path / "a.py"
        p.write_text("x = 1\n")
        editor.open_file(str(p))
        assert counts == [1]

    def test_open_twice_same_file(self, editor: EditorWidget, tmp_path: Path) -> None:
        p = tmp_path / "a.py"
        p.write_text("x = 1\n")
        editor.open_file(str(p))
        counts = _counts(editor)
        editor.open_file(str(p))  # та же вкладка
        # tabs_changed не эмитится, count не изменился.
        assert counts == []

    def test_close_last_tab_emits_zero(self, editor: EditorWidget, tmp_path: Path) -> None:
        p = tmp_path / "a.py"
        p.write_text("x = 1\n")
        editor.open_file(str(p))
        counts = _counts(editor)
        editor._on_tab_close_requested(0)
        assert counts == [0]

    def test_close_all_tabs_emits_zero(self, editor: EditorWidget, tmp_path: Path) -> None:
        for i in range(3):
            p = tmp_path / f"f{i}.py"
            p.write_text("x = 1\n")
            editor.open_file(str(p))
        counts = _counts(editor)
        editor.close_all_tabs()
        assert counts == [0]

    def test_close_file_silently_emits(self, editor: EditorWidget, tmp_path: Path) -> None:
        p = tmp_path / "a.py"
        p.write_text("x = 1\n")
        editor.open_file(str(p))
        counts = _counts(editor)
        editor.close_file_silently(str(p))
        assert counts == [0]


class TestBackToChatSignal:
    def test_button_exists(self, editor: EditorWidget) -> None:
        assert hasattr(editor, "back_to_chat_btn")
        assert editor.back_to_chat_btn.text() == "💬 Чат"

    def test_button_emits(self, editor: EditorWidget) -> None:
        received: list[bool] = []
        editor.back_to_chat_requested.connect(lambda: received.append(True))
        editor.back_to_chat_btn.click()
        assert received == [True]
