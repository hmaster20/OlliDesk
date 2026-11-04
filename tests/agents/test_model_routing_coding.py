"""10.6: выбор coder-модели."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agents.hardware_profile import CPUInfo, HardwareProfile, RAMInfo
from agents.slm_selector import select_coder
from core.config import SLMConfig, SLMModelInfo


class TestSelectCoder:
    def test_explicit_profile_models(self, tmp_path: Path) -> None:
        coder = SLMModelInfo(
            id="my-coder",
            repo="org/coder",
            file="coder.gguf",
            display_name="My Coder",
            tags=["chat", "coder"],
            tier=2,
            size_mb=4000,
        )
        with (
            patch.object(SLMConfig, "get_chat_models", return_value=[coder]),
            patch.object(
                SLMConfig, "get_model_by_id", side_effect=lambda mid: coder if mid == "my-coder" else None
            ),
            patch(
                "agents.download_slm.is_model_installed", return_value=True
            ),
        ):
            hw = HardwareProfile(
                cpu=CPUInfo(physical_cores=8, logical_cores=16),
                ram=RAMInfo(total_mb=32768, available_mb=30000),
            )
            info = select_coder(
                hw=hw, profile_coder_models=["my-coder"]
            )
            assert info is not None
            assert info.id == "my-coder"

    def test_auto_by_tag(self) -> None:
        coder = SLMModelInfo(
            id="coder-x",
            repo="org/c",
            file="c.gguf",
            display_name="C",
            tags=["chat", "coder"],
            tier=2,
            size_mb=3000,
        )
        chat = SLMModelInfo(
            id="chat-y",
            repo="org/ch",
            file="ch.gguf",
            display_name="Ch",
            tags=["chat"],
            tier=2,
            size_mb=3000,
        )
        with (
            patch.object(
                SLMConfig, "get_chat_models", return_value=[coder, chat]
            ),
            patch(
                "agents.download_slm.is_model_installed", return_value=True
            ),
        ):
            hw = HardwareProfile(
                cpu=CPUInfo(physical_cores=8, logical_cores=16),
                ram=RAMInfo(total_mb=32768, available_mb=30000),
            )
            info = select_coder(hw=hw)
            assert info is not None
            assert info.id == "coder-x"

    def test_fallback_when_no_coder(self) -> None:
        chat = SLMModelInfo(
            id="chat-y",
            repo="org/ch",
            file="ch.gguf",
            display_name="Ch",
            tags=["chat"],
            tier=2,
            size_mb=3000,
        )
        with (
            patch.object(SLMConfig, "get_chat_models", return_value=[chat]),
            patch(
                "agents.download_slm.is_model_installed", return_value=True
            ),
        ):
            hw = HardwareProfile(
                cpu=CPUInfo(physical_cores=8, logical_cores=16),
                ram=RAMInfo(total_mb=32768, available_mb=30000),
            )
            info = select_coder(hw=hw, allow_fallback=True)
            assert info is not None
            assert info.id == "chat-y"

    def test_no_fallback_returns_none(self) -> None:
        chat = SLMModelInfo(
            id="chat-y",
            repo="org/ch",
            file="ch.gguf",
            display_name="Ch",
            tags=["chat"],
            tier=2,
            size_mb=3000,
        )
        with (
            patch.object(SLMConfig, "get_chat_models", return_value=[chat]),
            patch(
                "agents.download_slm.is_model_installed", return_value=True
            ),
        ):
            hw = HardwareProfile(
                cpu=CPUInfo(physical_cores=8, logical_cores=16),
                ram=RAMInfo(total_mb=32768, available_mb=30000),
            )
            info = select_coder(hw=hw, allow_fallback=False)
            assert info is None