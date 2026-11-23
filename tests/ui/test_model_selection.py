"""0.1.4: модель за сессией / явный выбор.

Проверяем:
- при активном SLM смена режима/модели НЕ эмитит ``model`` в
  ``settings_changed`` (иначе ``last_model`` перетирался бы);
- при активной Ollama ``model`` эмитится;
- ``set_model`` (программный) не триггерит сохранение;
- ``SLMModelsDialog._activate_selected`` выключает ``auto_select_model``.
"""

from __future__ import annotations

import contextlib

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from agents.model_router import BackendType
from core.config import AppConfig, SLMConfig, SLMModelInfo
from state.session_store import SessionStore


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp, tmp_path):
    from ui.chat.panel import ChatPanel

    store = SessionStore(tmp_path / "sessions.db")
    p = ChatPanel(session_store=store, base_url="http://localhost:11434", model="test")
    # См. комментарий в test_history_save.py — изолируем GPU от QWebEngine-тестов.
    p._detect_backends = lambda: None  # type: ignore[method-assign]
    # По умолчанию active_backend в тесте будет RULE_BASED — тесты, которые
    # хотят SLM/Ollama, выставляют _active_backend через monkeypatch.
    yield p
    with contextlib.suppress(Exception):
        p.shutdown()
    p.deleteLater()


def _capture_settings(panel) -> list[dict]:
    """Подписывается на settings_changed и возвращает накопитель payload'ов."""
    captured: list[dict] = []
    panel.settings_changed.connect(captured.append)
    return captured


def test_save_current_settings_omits_model_when_slm_active(panel, monkeypatch):
    """SLM active → payload без ключа 'model'.

    В тестовой среде реальная SLM-модель не загружена (QTimer.singleShot
    на _detect_backends не отрабатывает), поэтому active_backend остаётся
    RULE_BASED. Для проверки ветки "SLM active" явно выставляем
    _active_backend через monkeypatch — как это уже делается для Ollama.
    """
    monkeypatch.setattr(panel._router, "_active_backend", BackendType.LOCAL_SLM)
    captured = _capture_settings(panel)

    panel._save_current_settings()

    assert captured, "settings_changed должен был эмитнуться"
    payload = captured[-1]
    assert "mode" in payload
    assert "model" not in payload


def test_set_model_does_not_persist_when_slm_active(panel, monkeypatch):
    """Программный set_model не эмитит 'model' при SLM-active."""
    monkeypatch.setattr(panel._router, "_active_backend", BackendType.LOCAL_SLM)
    captured = _capture_settings(panel)

    panel.set_model("some-ollama-model")

    # set_model сам по себе сохранение не вызывает — _on_model_changed
    # подавлен флагом _suppress_model_save.
    assert captured == []


def test_mode_change_omits_model_when_slm_active(panel, monkeypatch):
    """Переключение режима при SLM-active не перетирает last_model."""
    monkeypatch.setattr(panel._router, "_active_backend", BackendType.LOCAL_SLM)
    captured = _capture_settings(panel)

    panel.set_mode_by_name("plan")
    panel.set_mode_by_name("agent")

    # set_mode_by_name → mode_combo.setCurrentIndex → _on_mode_changed →
    # _emit_settings_changed. Payload не должен содержать 'model'.
    assert captured
    for payload in captured:
        assert "model" not in payload


def test_save_current_settings_includes_model_when_ollama_active(panel, monkeypatch):
    """Ollama active → payload содержит 'model'."""
    # Подменяем активный бэкенд (приватное поле — тест, контракт не трогаем).
    monkeypatch.setattr(panel._router, "_active_backend", BackendType.OLLAMA)
    captured = _capture_settings(panel)

    panel._save_current_settings()

    assert captured
    payload = captured[-1]
    assert "model" in payload
    assert payload["model"] == panel.get_current_model()


def test_manual_model_change_persists_when_ollama_active(panel, monkeypatch):
    """Ручная смена модели при Ollama active эмитит 'model'."""
    monkeypatch.setattr(panel._router, "_active_backend", BackendType.OLLAMA)
    captured = _capture_settings(panel)

    # Эмулируем ручную смену: пользователь выбрал другую модель в combo.
    # _on_model_changed вызывается из currentIndexChanged.
    panel._on_model_changed(0)

    assert captured
    payload = captured[-1]
    assert "model" in payload


def test_activate_selected_disables_auto_select(qapp, monkeypatch, tmp_path):
    """Явный выбор модели в SLMModelsDialog выключает auto_select_model."""
    from ui.dialogs import slm_models_dialog as dlg_mod

    cfg = AppConfig(
        default_model="test",
        embed_model="test",
        slm=SLMConfig(
            auto_select_model=True,
            selected_model="m1",
            available_models=[
                SLMModelInfo(id="m1", repo="r", file="f1.gguf", display_name="M1"),
                SLMModelInfo(id="m2", repo="r", file="f2.gguf", display_name="M2"),
            ],
        ),
    )

    # Моки: «все модели установлены», save_config — no-op.
    monkeypatch.setattr("agents.download_slm.is_model_installed", lambda *a, **kw: True)
    monkeypatch.setattr(dlg_mod, "is_model_installed", lambda *a, **kw: True)
    monkeypatch.setattr(dlg_mod, "save_config", lambda *a, **kw: None)
    # _real_size_str читает get_models_dir() — подменяем на пустую tmp_path.
    monkeypatch.setattr(dlg_mod, "get_models_dir", lambda: tmp_path)

    dlg = dlg_mod.SLMModelsDialog(cfg)
    try:
        # Найти m2 и сделать текущей.
        for row in range(dlg.table.rowCount()):
            item = dlg.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == "m2":
                dlg.table.setCurrentCell(row, 0)
                break

        dlg._activate_selected()

        assert cfg.slm.selected_model == "m2"
        assert cfg.slm.auto_select_model is False
    finally:
        dlg.close()
        dlg.deleteLater()
