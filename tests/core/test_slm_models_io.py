"""9.3.3: I/O каталога SLM-моделей."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.config import SLMModelInfo
from core.slm_models_io import (
    SCHEMA_VERSION,
    ensure_slm_models_file,
    load_slm_models,
    merge_with_defaults,
    save_slm_models,
)


def _info(id_: str, **kwargs) -> SLMModelInfo:
    defaults = {
        "repo": "org/repo",
        "file": f"{id_}.gguf",
        "display_name": id_,
    }
    defaults.update(kwargs)
    return SLMModelInfo(id=id_, **defaults)


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_slm_models(tmp_path / "missing.yaml") is None


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "slm_models.yaml"
    models = [
        _info("m1", tags=["chat", "coder"]),
        _info("m2", tier=3, description="X"),
    ]
    save_slm_models(models, path)
    loaded = load_slm_models(path)
    assert loaded is not None
    assert len(loaded) == 2
    assert loaded[0].id == "m1"
    assert "coder" in loaded[0].tags
    assert loaded[1].description == "X"


def test_save_writes_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "slm_models.yaml"
    save_slm_models([_info("m")], path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION


def test_load_skips_invalid_entries(tmp_path: Path) -> None:
    path = tmp_path / "slm_models.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "models": [
                    {"id": "ok", "repo": "r", "file": "f", "display_name": "OK"},
                    {"id": "broken"},  # нет repo/file/display_name
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_slm_models(path)
    assert loaded is not None
    assert [m.id for m in loaded] == ["ok"]


def test_load_broken_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "slm_models.yaml"
    path.write_text("models: [broken", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        load_slm_models(path)


def test_merge_adds_new_defaults() -> None:
    loaded = [_info("old", description="user")]
    defaults = [_info("old"), _info("new")]
    merged = merge_with_defaults(loaded, defaults)
    ids = [m.id for m in merged]
    assert ids == ["old", "new"]


def test_merge_preserves_user_display_name() -> None:
    loaded = [_info("m", display_name="Моё имя")]
    defaults = [_info("m", display_name="Дефолт")]
    merged = merge_with_defaults(loaded, defaults)
    assert merged[0].display_name == "Моё имя"


def test_merge_preserves_user_only_models() -> None:
    loaded = [_info("custom")]
    defaults = [_info("builtin")]
    merged = merge_with_defaults(loaded, defaults)
    ids = {m.id for m in merged}
    assert ids == {"builtin", "custom"}


def test_ensure_creates_file_from_defaults(tmp_path: Path) -> None:
    path = tmp_path / "slm_models.yaml"
    defaults = [_info("m1"), _info("m2")]
    result = ensure_slm_models_file(defaults, path)
    assert path.exists()
    assert {m.id for m in result} == {"m1", "m2"}


def test_ensure_merges_existing(tmp_path: Path) -> None:
    path = tmp_path / "slm_models.yaml"
    save_slm_models([_info("old")], path)
    defaults = [_info("old"), _info("new")]
    result = ensure_slm_models_file(defaults, path)
    ids = {m.id for m in result}
    assert ids == {"old", "new"}