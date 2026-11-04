"""2.8: ModelService — config + park/coder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.config import AppConfig, SLMConfig, SLMModelInfo
from core.profiles import ProfileConfig, ProfileToolPolicies
from core.services.model_service import ModelService


def _cfg() -> AppConfig:
    return AppConfig(
        default_model="m",
        embed_model="e",
        slm=SLMConfig(available_models=[
            SLMModelInfo(
                id="coder-x", repo="r", file="c.gguf",
                display_name="C", tags=["chat", "coder"], kind="chat",
            ),
        ]),
    )


class TestConfig:
    def test_get_config_caches(self) -> None:
        svc = ModelService()
        with patch("core.services.model_service.load_config") as mock_load:
            mock_load.return_value = _cfg()
            svc.get_config()
            svc.get_config()
            assert mock_load.call_count == 1

    def test_reload_forces(self) -> None:
        svc = ModelService()
        with patch("core.services.model_service.load_config") as mock_load:
            mock_load.return_value = _cfg()
            svc.get_config()
            svc.reload_config()
            assert mock_load.call_count == 2


class TestResolveCoder:
    def test_explicit_profile_coder(self) -> None:
        cfg = _cfg()
        svc = ModelService(config=cfg)
        profile = ProfileConfig(
            id="development",
            name="Dev",
            freedom_level="development",
            tool_policies=ProfileToolPolicies(),
            coder_models=["coder-x"],
        )
        with patch(
            "agents.download_slm.is_model_installed", return_value=True
        ):
            info = svc.resolve_coder(profile)
            assert info is not None
            assert info.id == "coder-x"


class TestResolvePaths:
    def test_returns_tuple(self) -> None:
        svc = ModelService(config=_cfg())
        with patch(
            "core.services.model_service.get_model_paths",
            return_value=(Path("/a.gguf"), Path("/b.gguf")),
        ):
            chat, embed = svc.resolve_paths()
            assert chat == Path("/a.gguf")
            assert embed == Path("/b.gguf")