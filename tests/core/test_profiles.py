"""Тесты профилей использования (9.4)."""

from __future__ import annotations

import pytest

from core.config import AppConfig
from core.profiles import (
    ProfileConfig,
    ProfileToolPolicies,
    builtin_profile_ids,
    default_profiles,
    get_default_profile,
    get_profile_by_id,
)


class TestProfileConfig:
    def test_default_profiles_count(self) -> None:
        profiles = default_profiles()
        assert len(profiles) == 3
        ids = {p.id for p in profiles}
        assert ids == {"development", "research", "learning"}

    def test_all_builtin(self) -> None:
        for p in default_profiles():
            assert p.builtin is True

    def test_development_policies(self) -> None:
        dev = get_profile_by_id(default_profiles(), "development")
        assert dev is not None
        assert dev.agent_mode == "agent"
        assert dev.rag_enabled is True
        assert dev.tool_policies.write_file == "ask_first"

    def test_research_excludes_write(self) -> None:
        research = get_profile_by_id(default_profiles(), "research")
        assert research is not None
        assert research.agent_mode == "plan"
        assert research.tool_policies.write_file == "excluded"
        assert research.tool_policies.run_shell_command == "excluded"
        assert research.tool_policies.web_search == "auto"

    def test_learning_all_excluded(self) -> None:
        learning = get_profile_by_id(default_profiles(), "learning")
        assert learning is not None
        assert learning.agent_mode == "chat"
        assert learning.rag_enabled is False
        policies = learning.tool_policies.model_dump()
        assert all(v == "excluded" for v in policies.values())

    def test_default_is_development(self) -> None:
        assert get_default_profile(default_profiles()).id == "development"

    def test_get_by_id_returns_none_for_unknown(self) -> None:
        assert get_profile_by_id(default_profiles(), "unknown") is None

    def test_builtin_ids(self) -> None:
        assert builtin_profile_ids() == {"development", "research", "learning"}

    def test_tool_policies_validation(self) -> None:
        with pytest.raises(ValueError):
            ProfileToolPolicies(write_file="invalid")  # type: ignore[arg-type]


class TestAppConfigProfiles:
    def test_defaults_include_builtin_profiles(self) -> None:
        cfg = AppConfig(default_model="m", embed_model="e")
        assert len(cfg.profiles) >= 3
        assert cfg.active_profile == "development"
        assert get_profile_by_id(cfg.profiles, "development") is not None

    def test_sync_builtin_updates_from_code(self) -> None:
        cfg = AppConfig(
            default_model="m",
            embed_model="e",
            profiles=[
                ProfileConfig(
                    id="development",
                    name="Устаревшее имя",
                    builtin=True,
                )
            ],
        )
        # Встроенные профили синхронизируются с кодом.
        dev = get_profile_by_id(cfg.profiles, "development")
        assert dev is not None
        assert dev.name == "Разработка"  # из _DEFAULT_PROFILES

    def test_custom_profiles_preserved(self) -> None:
        custom = ProfileConfig(
            id="my-profile",
            name="Мой профиль",
            builtin=False,
            agent_mode="plan",
        )
        cfg = AppConfig(
            default_model="m",
            embed_model="e",
            profiles=[custom],
        )
        assert get_profile_by_id(cfg.profiles, "my-profile") is not None
        # И встроенные тоже на месте.
        assert get_profile_by_id(cfg.profiles, "development") is not None

    def test_invalid_active_profile_falls_back(self) -> None:
        cfg = AppConfig(
            default_model="m",
            embed_model="e",
            active_profile="nonexistent",
        )
        assert cfg.active_profile == "development"

    def test_documents_defaults(self) -> None:
        cfg = AppConfig(default_model="m", embed_model="e")
        assert cfg.index_documents is True
        assert cfg.max_document_size_mb == 50
        assert cfg.document_ocr is False
        assert ".pdf" in cfg.index_extensions
        assert ".docx" in cfg.index_extensions