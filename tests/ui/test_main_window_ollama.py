"""Ollama-проверки вынесены из MainWindow в OllamaController."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_ollama_code_moved_out() -> None:
    src = (
        Path(__file__).parent.parent.parent / "ui" / "main_window.py"
    ).read_text(encoding="utf-8")
    for name in (
        "class ModelCheckThread",
        "def _do_check_ollama",
        "def _on_ollama_checked",
        "def _check_ollama_connection",
        "def _stop_model_check",
        "_is_checking",
        "_available_models",
    ):
        assert name not in src, f"{name} должен быть в ui/ollama_controller.py"


def test_controller_api() -> None:
    pytest.importorskip("PySide6")
    from ui.ollama_controller import ModelCheckThread, OllamaController

    for name in ("check_connection", "toggle_tools_check", "stop_tools_check", "shutdown"):
        assert hasattr(OllamaController, name)
    assert hasattr(ModelCheckThread, "check_finished")