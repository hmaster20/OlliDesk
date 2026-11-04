"""9.3.1: HF search — detect_tags, кэш, оффлайн-fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.hf_search import (
    HFGgufFile,
    HFModel,
    HFModelDetails,
    HFUnavailableError,
    clear_caches,
    detect_tags,
    search_models,
)


class TestDetectTags:
    def test_coder(self) -> None:
        assert "coder" in detect_tags(["code", "starcoder"], "", "")

    def test_coder_from_repo_id(self) -> None:
        assert "coder" in detect_tags([], "", "Qwen2.5-Coder-14B")

    def test_reasoning(self) -> None:
        assert "reasoning" in detect_tags(["r1"], "", "")

    def test_moe(self) -> None:
        assert "moE" in detect_tags([], "", "Qwen3-Coder-30B-A3B")

    def test_embed(self) -> None:
        assert "embed" in detect_tags(["bge"], "", "")

    def test_default_chat(self) -> None:
        tags = detect_tags([], "", "some-model")
        assert "chat" in tags

    def test_coder_wins_over_chat(self) -> None:
        tags = detect_tags(["coder"], "", "")
        assert "coder" in tags
        # chat не добавляется, если есть coder.
        assert "chat" not in tags or tags.count("chat") == 0

    def test_dedup(self) -> None:
        tags = detect_tags(["code", "coder"], "", "")
        # Не важно, сколько раз встретилось «coder» — один в списке.
        assert tags.count("coder") == 1


class TestSearchModels:
    @pytest.mark.asyncio
    async def test_offline_raises(self) -> None:
        clear_caches()
        with patch(
            "agents.hf_search._get_api",
            side_effect=HFUnavailableError("нет сети"),
        ):
            with pytest.raises(HFUnavailableError, match="нет сети"):
                await search_models("qwen")

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        clear_caches()
        mock_model = MagicMock()
        mock_model.id = "test/model"
        mock_model.downloads = 100
        mock_model.likes = 5
        mock_model.tags = ["gguf"]
        mock_model.pipeline_tag = "text-generation"
        mock_model.last_modified = "2025-01-01"

        mock_api = MagicMock()
        mock_api.list_models.return_value = iter([mock_model])

        with patch("agents.hf_search._get_api", return_value=mock_api):
            r1 = await search_models("qwen", limit=10)
            r2 = await search_models("qwen", limit=10)

        assert len(r1) == 1
        assert r1[0].repo_id == "test/model"
        # Второй вызов — из кэша, API не вызывается.
        assert mock_api.list_models.call_count == 1


class TestHFModelDetails:
    def test_is_gguf(self) -> None:
        d = HFModelDetails(
            repo_id="x/y",
            gguf_files=[HFGgufFile("model.gguf", 1024)],
        )
        assert d.is_gguf

    def test_size_human(self) -> None:
        f = HFGgufFile("m.gguf", size_bytes=5_000_000_000)
        assert "ГБ" in f.size_human
        f2 = HFGgufFile("m.gguf", size_bytes=500_000_000)
        assert "МБ" in f2.size_human