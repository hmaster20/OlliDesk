"""Тесты MRO ChatPanel после выноса миксинов (2.4).

Без pytest-qt: проверяем только MRO — никакого QApplication не требуется.
"""

from __future__ import annotations

import pytest


def test_mro_order() -> None:
    """QWidget первым, миксины после. Иначе PySide6 не работает."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QWidget

    from ui.chat.backends import BackendsMixin
    from ui.chat.panel import ChatPanel
    from ui.chat.research import ResearchMixin

    mro = ChatPanel.__mro__
    # QWidget должен быть раньше миксинов (PySide6 требует).
    qwidget_idx = mro.index(QWidget)
    backends_idx = mro.index(BackendsMixin)
    research_idx = mro.index(ResearchMixin)
    assert qwidget_idx < backends_idx < research_idx


def test_methods_from_mixins() -> None:
    """Ключевые методы доступны на ChatPanel через миксины."""
    pytest.importorskip("PySide6")
    from ui.chat.panel import ChatPanel

    # Из BackendsMixin.
    assert hasattr(ChatPanel, "reload_slm")
    assert hasattr(ChatPanel, "_detect_and_load_slm")
    assert hasattr(ChatPanel, "_load_slm_async")
    assert hasattr(ChatPanel, "_on_slm_loaded")
    assert hasattr(ChatPanel, "_detect_backends")
    assert hasattr(ChatPanel, "_apply_backend_config")

    # Из ResearchMixin.
    assert hasattr(ChatPanel, "reset_pipeline")
    assert hasattr(ChatPanel, "_ensure_orchestrator")
    assert hasattr(ChatPanel, "_on_pool_loaded")
    assert hasattr(ChatPanel, "_on_orchestrator_result")
    assert hasattr(ChatPanel, "_on_thought_chunk")
    assert hasattr(ChatPanel, "_show_research_passport")
    assert hasattr(ChatPanel, "_pipeline_status_label")


def test_panel_size_reduced() -> None:
    """panel.py не должен содержать определения вынесенных методов.

    Грубая проверка через исходник: `def _on_slm_loaded` и т.п. не должны
    появляться в panel.py. Это защита от копипасты при следующих правках.
    """
    from pathlib import Path

    panel_path = Path(__file__).parent.parent.parent / "ui" / "chat" / "panel.py"
    source = panel_path.read_text(encoding="utf-8")

    moved_defs = [
        "def _on_slm_loaded",
        "def _detect_and_load_slm",
        "def _load_slm_async",
        "def _download_and_load_slm",
        "def _on_pool_loaded",
        "def _on_orchestrator_result",
        "def _on_thought_chunk",
        "def _show_research_passport",
        "def _pipeline_status_label",
        "def _ensure_orchestrator",
        "def reset_pipeline",
    ]
    for name in moved_defs:
        assert name not in source, f"{name} должен быть в backends.py/research.py, а не в panel.py"
