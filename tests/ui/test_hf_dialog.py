"""9.3.1: HFDialog — оффлайн + подписки на сигналы."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from ui.dialogs.hf_dialog import HFDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp):
    dlg = HFDialog()
    yield dlg
    dlg.close()
    dlg.deleteLater()


class TestInitialState:
    def test_download_btn_disabled(self, dialog: HFDialog) -> None:
        assert not dialog.download_btn.isEnabled()

    def test_add_catalog_btn_disabled(self, dialog: HFDialog) -> None:
        assert not dialog.add_catalog_btn.isEnabled()

    def test_details_empty(self, dialog: HFDialog) -> None:
        assert dialog._current_details is None


class TestSearchError:
    def test_error_shown(self, dialog: HFDialog) -> None:
        # Прямой вызов слота с ошибкой (без сети).
        dialog._on_search_error("HF недоступен")
        assert "HF недоступен" in dialog.status_label.text()
        # Прогресс скрыт, кнопка снова активна.
        assert not dialog.progress.isVisible()
        assert dialog.search_btn.isEnabled()


class TestSearchDone:
    def test_populates_list(self, dialog: HFDialog) -> None:
        from agents.hf_search import HFModel

        models = [
            HFModel(
                repo_id="Qwen/Qwen2.5-Coder-14B-GGUF",
                downloads=1000,
                likes=50,
                tags=["gguf", "coder"],
            ),
            HFModel(
                repo_id="meta-llama/Llama-3.1-8B",
                downloads=500,
                likes=20,
            ),
        ]
        dialog._on_search_done(models)
        assert dialog.results_list.count() == 2
        # Первая — coder (badge в тексте).
        first_text = dialog.results_list.item(0).text()
        assert "Qwen" in first_text
        assert "coder" in first_text


class TestSignals:
    def test_download_requested_emitted(self, dialog: HFDialog) -> None:
        from agents.hf_search import HFGgufFile, HFModelDetails

        dialog._current_details = HFModelDetails(
            repo_id="Qwen/Qwen2.5-Coder-14B-GGUF",
            tags=["gguf", "coder"],
            gguf_files=[HFGgufFile("model.gguf", 1_000_000)],
        )
        dialog.files_table.setRowCount(1)
        from PySide6.QtWidgets import QTableWidgetItem

        dialog.files_table.setItem(0, 0, QTableWidgetItem("model.gguf"))
        dialog.files_table.selectRow(0)

        received: list[dict] = []
        dialog.download_requested.connect(received.append)
        dialog._on_download()
        assert len(received) == 1
        payload = received[0]
        assert payload["repo_id"] == "Qwen/Qwen2.5-Coder-14B-GGUF"
        assert payload["filename"] == "model.gguf"
        assert "coder" in payload["tags"]

    def test_add_to_catalog_requested_emitted(self, dialog: HFDialog) -> None:
        from agents.hf_search import HFGgufFile, HFModelDetails

        dialog._current_details = HFModelDetails(
            repo_id="Qwen/Qwen2.5-Coder-14B-GGUF",
            tags=["gguf"],
            description="test description",
            gguf_files=[HFGgufFile("model.gguf", 2_000_000_000)],
        )
        dialog.files_table.setRowCount(1)
        from PySide6.QtWidgets import QTableWidgetItem

        dialog.files_table.setItem(0, 0, QTableWidgetItem("model.gguf"))
        dialog.files_table.selectRow(0)

        received: list[dict] = []
        dialog.add_to_catalog_requested.connect(received.append)
        dialog._on_add_to_catalog()
        assert len(received) == 1
        payload = received[0]
        assert payload["repo_id"] == "Qwen/Qwen2.5-Coder-14B-GGUF"
        assert payload["filename"] == "model.gguf"
        assert payload["size_mb"] == 1907  # 2_000_000_000 / (1024*1024)