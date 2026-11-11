"""9.2: автопоказ структуры при первом открытии проекта."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from core.config import AppConfig


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def main_window(qapp, tmp_path: Path):
    from ui.main_window import MainWindow

    cfg = AppConfig(
        default_model="m",
        embed_model="e",
        ignored_patterns=[],
        index_extensions=[".py"],
        recent_projects=[],
    )
    w = MainWindow(cfg)
    # Подавляем фоновые потоки.
    w.chat_panel._detect_backends = lambda: None  # type: ignore[method-assign]
    w.chat_panel._start_mcp_init = lambda: None  # type: ignore[method-assign]
    w.chat_panel._recover_inbox_on_startup = lambda: None  # type: ignore[method-assign]
    w.indexing_controller.start = lambda: None  # type: ignore[method-assign]
    yield w
    with contextlib.suppress(Exception):
        w.close()


class TestFirstOpen:
    def test_first_open_shows_tree(self, main_window, tmp_path: Path) -> None:
        project = tmp_path / "proj1"
        project.mkdir()
        (project / "main.py").write_text("x=1")
        main_window._do_open_project(project)
        assert main_window.left_panel.isVisibleTo(main_window)

    def test_second_open_respects_saved(self, main_window, tmp_path: Path) -> None:
        # Добавляем проект в recent_projects «как будто открывали раньше».
        project = tmp_path / "proj2"
        project.mkdir()
        (project / "main.py").write_text("x=1")
        main_window.config.recent_projects = [str(project)]
        main_window.config.show_project_tree = False
        main_window._do_open_project(project)
        # Пользователь ранее скрыл — уважаем.
        assert not main_window.left_panel.isVisibleTo(main_window)
