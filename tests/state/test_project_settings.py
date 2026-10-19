"""Тесты ProjectSettings: round-trip show_reasoning + обратная совместимость.

Ключевые инварианты:
* `show_reasoning=False` сохраняется и читается.
* Старый `.yaml` без поля `show_reasoning` читается (дефолт True).
* Битые YAML-данные не падают, возвращается пустой ProjectSettings.
"""

from __future__ import annotations

from pathlib import Path

from state.project_settings import ProjectSettings


class TestShowReasoningRoundTrip:
    def test_default_is_true(self) -> None:
        s = ProjectSettings()
        assert s.show_reasoning is True

    def test_save_load_false(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        original = ProjectSettings(
            model="qwen2.5-coder:7b",
            mode="agent",
            show_reasoning=False,
        )
        original.save(proj)
        loaded = ProjectSettings.load(proj)
        assert loaded.show_reasoning is False
        assert loaded.model == "qwen2.5-coder:7b"
        assert loaded.mode == "agent"

    def test_save_load_true(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        ProjectSettings(show_reasoning=True).save(proj)
        loaded = ProjectSettings.load(proj)
        assert loaded.show_reasoning is True

    def test_save_creates_dir(self, tmp_path: Path) -> None:
        proj = tmp_path / "fresh"
        # Каталога proj ещё нет — save должен создать .ollidesk.
        ProjectSettings().save(proj)
        assert (proj / ".ollidesk" / "project_settings.yaml").exists()


class TestBackwardCompatibility:
    def test_old_yaml_without_show_reasoning(self, tmp_path: Path) -> None:
        """Старый .yaml (созданный до появления поля) читается без ошибок."""
        proj = tmp_path / "proj"
        (proj / ".ollidesk").mkdir(parents=True)
        (proj / ".ollidesk" / "project_settings.yaml").write_text(
            "model: qwen2.5-coder:7b\nmode: chat\ntemperature: 0.7\nmax_tokens: 4096\n",
            encoding="utf-8",
        )
        loaded = ProjectSettings.load(proj)
        assert loaded.model == "qwen2.5-coder:7b"
        assert loaded.mode == "chat"
        assert loaded.show_reasoning is True  # дефолт

    def test_missing_file(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        loaded = ProjectSettings.load(proj)
        assert loaded.show_reasoning is True
        assert loaded.model == ""

    def test_corrupt_yaml(self, tmp_path: Path) -> None:
        """Битый YAML → пустой ProjectSettings (не падаем)."""
        proj = tmp_path / "proj"
        (proj / ".ollidesk").mkdir(parents=True)
        (proj / ".ollidesk" / "project_settings.yaml").write_text(
            "{[ broken yaml : :", encoding="utf-8"
        )
        loaded = ProjectSettings.load(proj)
        assert loaded.show_reasoning is True
        assert loaded.model == ""

    def test_extra_unknown_fields_ignored(self, tmp_path: Path) -> None:
        """Pydantic BaseModel по умолчанию игнорирует лишние поля."""
        proj = tmp_path / "proj"
        (proj / ".ollidesk").mkdir(parents=True)
        (proj / ".ollidesk" / "project_settings.yaml").write_text(
            "model: m\nfuture_field: 42\nshow_reasoning: false\n",
            encoding="utf-8",
        )
        # Pydantic BaseModel с дефолтным config — extra=ignore по умолчанию.
        loaded = ProjectSettings.load(proj)
        assert loaded.show_reasoning is False