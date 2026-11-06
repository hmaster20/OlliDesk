"""2.7: session overrides."""

from __future__ import annotations

from core.config import AppConfig, ToolPolicy


def test_session_overrides_roundtrip() -> None:
    cfg = AppConfig(default_model="m", embed_model="e")
    cfg.session_overrides = {"run_shell_command": ToolPolicy.AUTO}
    restored = AppConfig.model_validate(cfg.model_dump())
    assert restored.session_overrides["run_shell_command"] == ToolPolicy.AUTO


def test_session_overrides_default_empty() -> None:
    cfg = AppConfig(default_model="m", embed_model="e")
    assert cfg.session_overrides == {}
