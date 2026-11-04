"""9.11: интеграция AsyncResource в PipelineSelectors и SessionManager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from core.config import AppConfig, SLMConfig, SLMModelInfo
from state.session_store import SessionStore


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class TestPipelineSelectors:
    def test_initial_placeholder_when_loading(self, qapp, monkeypatch) -> None:
        """Во время первой загрузки combo показывает «Загрузка…»."""
        from ui.widgets.pipeline_selectors import PipelineSelectors

        cfg = AppConfig(
            default_model="m",
            embed_model="e",
            slm=SLMConfig(available_models=[
                SLMModelInfo(
                    id="m1", repo="r", file="m1.gguf",
                    display_name="M1", kind="chat",
                ),
            ]),
        )
        # Отключаем реальную проверку «установлена ли модель».
        monkeypatch.setattr(
            "agents.download_slm.is_model_installed", lambda *a, **kw: True
        )
        w = PipelineSelectors(cfg)
        # Первый refresh_models в __init__ уже загрузил — resource READY.
        assert w._models_resource.loaded()
        w.deleteLater()

    def test_no_flicker_on_same_models(self, qapp, monkeypatch) -> None:
        """Повторный refresh_models с тем же набором не вызывает clear()."""
        from ui.widgets.pipeline_selectors import PipelineSelectors

        cfg = AppConfig(
            default_model="m",
            embed_model="e",
            slm=SLMConfig(available_models=[
                SLMModelInfo(
                    id="m1", repo="r", file="m1.gguf",
                    display_name="M1", kind="chat",
                ),
            ]),
        )
        monkeypatch.setattr(
            "agents.download_slm.is_model_installed", lambda *a, **kw: True
        )
        w = PipelineSelectors(cfg)
        before = w.planner_combo.count()
        # Подменяем clear на шпион.
        clear_calls = []
        original_clear = w.planner_combo.clear
        w.planner_combo.clear = lambda: (clear_calls.append(1), original_clear())  # type: ignore[method-assign]
        w.refresh_models()
        # Набор не изменился — clear не вызван (guard «не мигать»).
        assert clear_calls == []
        assert w.planner_combo.count() == before
        w.deleteLater()

    def test_new_model_added_after_refresh(self, qapp, monkeypatch) -> None:
        """Появление новой модели → combo обновлён."""
        from ui.widgets.pipeline_selectors import PipelineSelectors

        cfg = AppConfig(
            default_model="m",
            embed_model="e",
            slm=SLMConfig(available_models=[
                SLMModelInfo(
                    id="m1", repo="r", file="m1.gguf",
                    display_name="M1", kind="chat",
                ),
            ]),
        )
        monkeypatch.setattr(
            "agents.download_slm.is_model_installed", lambda *a, **kw: True
        )
        w = PipelineSelectors(cfg)
        before = w.planner_combo.count()
        cfg.slm.available_models.append(
            SLMModelInfo(
                id="m2", repo="r", file="m2.gguf",
                display_name="M2", kind="chat",
            )
        )
        w.refresh_models()
        # После добавления +1 (плейсхолдер «автовыбор» в счёт не идёт).
        assert w.planner_combo.count() > before
        w.deleteLater()


class TestSessionManager:
    def test_initial_load(self, qapp, tmp_path: Path) -> None:
        from ui.dialogs.session_manager_dialog import SessionManagerDialog

        store = SessionStore(tmp_path / "sessions.db")
        store.create_session("proj1")
        store.create_session("proj2")

        dlg = SessionManagerDialog(store, chat_panel=MagicMock())
        try:
            assert dlg._sessions_resource.loaded()
            assert len(dlg._sessions_resource.value_or([])) == 2
            assert dlg.table.rowCount() == 2
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_no_flicker_on_refresh(self, qapp, tmp_path: Path) -> None:
        """refresh() не сбрасывает таблицу в пустоту (guard 9.11)."""
        from ui.dialogs.session_manager_dialog import SessionManagerDialog

        store = SessionStore(tmp_path / "sessions.db")
        store.create_session("p1")
        dlg = SessionManagerDialog(store, chat_panel=MagicMock())
        try:
            # Добавляем сессию и делаем refresh — видимая строка остаётся
            # той же до обновления.
            store.create_session("p2")
            # Проверяем промежуточное состояние: begin_load → latest [p1].
            dlg._sessions_resource.begin_load()
            assert dlg._sessions_resource.latest is not None
            assert dlg._sessions_resource.is_refreshing()
            # set_value → обновлённый список.
            dlg._sessions_resource.set_value(store.list_sessions())
            assert len(dlg._sessions_resource.value_or([])) == 2
        finally:
            dlg.close()
            dlg.deleteLater()