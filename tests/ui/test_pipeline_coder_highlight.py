"""9.3.2: подсветка coder-моделей в PipelineSelectors."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.config import AppConfig, SLMConfig, SLMModelInfo


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _cfg() -> AppConfig:
    return AppConfig(
        default_model="m",
        embed_model="e",
        slm=SLMConfig(
            available_models=[
                SLMModelInfo(
                    id="coder-model",
                    repo="r",
                    file="c.gguf",
                    display_name="Coder Model",
                    tags=["chat", "coder"],
                    kind="chat",
                ),
                SLMModelInfo(
                    id="plain-model",
                    repo="r",
                    file="p.gguf",
                    display_name="Plain Model",
                    tags=["chat"],
                    kind="chat",
                ),
            ]
        ),
    )


class TestCoderHighlight:
    def test_development_highlights(self, qapp) -> None:
        from ui.widgets.pipeline_selectors import PipelineSelectors

        with patch("agents.download_slm.is_model_installed", return_value=True):
            w = PipelineSelectors(_cfg())
            w.set_active_profile("development")
            # Найдём coder-model в planner_combo.
            found = False
            for i in range(w.planner_combo.count()):
                if w.planner_combo.itemData(i) == "coder-model":
                    font = w.planner_combo.itemData(i, Qt.ItemDataRole.FontRole)
                    assert font is not None
                    assert font.bold()
                    found = True
                    break
            assert found, "coder-model не найден в combo"
            w.deleteLater()

    def test_research_no_highlight(self, qapp) -> None:
        from ui.widgets.pipeline_selectors import PipelineSelectors

        with patch("agents.download_slm.is_model_installed", return_value=True):
            w = PipelineSelectors(_cfg())
            w.set_active_profile("research")
            for i in range(w.planner_combo.count()):
                if w.planner_combo.itemData(i) == "coder-model":
                    font = w.planner_combo.itemData(i, Qt.ItemDataRole.FontRole)
                    if font is not None:
                        assert not font.bold()
                    break
            w.deleteLater()

    def test_role_icons_in_label(self, qapp) -> None:
        from ui.widgets.pipeline_selectors import PipelineSelectors

        with patch("agents.download_slm.is_model_installed", return_value=True):
            w = PipelineSelectors(_cfg())
            # coder-model должен иметь ⚡ в начале названия.
            for i in range(w.planner_combo.count()):
                if w.planner_combo.itemData(i) == "coder-model":
                    assert "⚡" in w.planner_combo.itemText(i)
                    break
            w.deleteLater()
