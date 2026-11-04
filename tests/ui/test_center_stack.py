"""9.1: чат — базовый экран, редактор — вкладки только для файлов."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from state.session_store import SessionStore


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def main_window(qapp, tmp_path: Path):
    """Собирает MainWindow с моками (без реальной индексации)."""
    from ui.main_window import MainWindow

    cfg = AppConfig(
        default_model="test",
        embed_model="test",
        ignored_patterns=[],
        index_extensions=[".py"],
    )
    # Подавляем фоновые потоки, чтобы тест не висел.
    with contextlib.suppress(Exception):
        pass
    window = MainWindow(cfg)
    # Отключаем фоновые потоки (MCP, SLM, Ollama).
    window.chat_panel._detect_backends = lambda: None  # type: ignore[method-assign]
    window.chat_panel._start_mcp_init = lambda: None  # type: ignore[method-assign]
    window.chat_panel._recover_inbox_on_startup = lambda: None  # type: ignore[method-assign]
    yield window
    with contextlib.suppress(Exception):
        window.close()


class TestInitialState:
    def test_chat_is_default_page(self, main_window) -> None:
        """При старте активна страница чата (индекс 0)."""
        assert main_window.center_stack.currentIndex() == 0

    def test_no_tab_widget(self, main_window) -> None:
        """QTabWidget в MainWindow больше нет (9.1)."""
        assert not hasattr(main_window, "tab_widget")

    def test_editor_has_zero_tabs(self, main_window) -> None:
        assert main_window.editor_widget.tab_count() == 0


class TestEditorOpens:
    def test_open_file_switches_to_editor(
        self, main_window, tmp_path: Path
    ) -> None:
        target = tmp_path / "test.py"
        target.write_text("x = 1\n")
        main_window.open_file_in_editor(str(target))
        assert main_window.center_stack.currentIndex() == 1
        assert main_window.editor_widget.tab_count() == 1

    def test_close_last_tab_switches_to_chat(
        self, main_window, tmp_path: Path
    ) -> None:
        target = tmp_path / "test.py"
        target.write_text("x = 1\n")
        main_window.open_file_in_editor(str(target))
        # Закрываем единственную вкладку.
        main_window.editor_widget._on_tab_close_requested(0)
        assert main_window.center_stack.currentIndex() == 0
        assert main_window.editor_widget.tab_count() == 0


class TestShowChatTab:
    def test_show_chat_from_editor(self, main_window, tmp_path: Path) -> None:
        target = tmp_path / "test.py"
        target.write_text("x = 1\n")
        main_window.open_file_in_editor(str(target))
        assert main_window.center_stack.currentIndex() == 1
        main_window._show_chat_tab()
        assert main_window.center_stack.currentIndex() == 0
        # Вкладка файла сохранилась.
        assert main_window.editor_widget.tab_count() == 1


class TestShowEditorTab:
    def test_show_editor_without_files_is_noop(self, main_window) -> None:
        """Без открытых файлов редактор не открывается."""
        main_window._show_chat_tab()
        main_window._show_editor_tab()
        assert main_window.center_stack.currentIndex() == 0

    def test_show_editor_with_file(self, main_window, tmp_path: Path) -> None:
        target = tmp_path / "test.py"
        target.write_text("x = 1\n")
        main_window.open_file_in_editor(str(target))
        main_window._show_chat_tab()
        assert main_window.center_stack.currentIndex() == 0
        main_window._show_editor_tab()
        assert main_window.center_stack.currentIndex() == 1


class TestBackToChatButton:
    def test_button_emits_signal(self, main_window) -> None:
        """Кнопка «💬 Чат» в тулбаре редактора эмитит сигнал.

        Проверяем только наличие сигнала в API (без клика — на клик
        сработает _show_chat_tab, это покрыто выше).
        """
        assert hasattr(main_window.editor_widget, "back_to_chat_btn")
        assert hasattr(main_window.editor_widget, "back_to_chat_requested")