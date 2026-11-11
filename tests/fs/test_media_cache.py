"""9.7: media_cache — hit/miss по sha256."""

from __future__ import annotations

from pathlib import Path

from fs.media_cache import get_cached, set_cached


class TestCache:
    def test_miss_then_hit(self, tmp_path: Path) -> None:
        p = tmp_path / "a.png"
        p.write_bytes(b"fake image")
        assert get_cached(p, "image") is None
        set_cached(p, "image", "recognized text")
        assert get_cached(p, "image") == "recognized text"

    def test_different_kind_different_cache(self, tmp_path: Path) -> None:
        p = tmp_path / "a.png"
        p.write_bytes(b"x")
        set_cached(p, "image", "img text")
        set_cached(p, "audio", "audio text")
        assert get_cached(p, "image") == "img text"
        assert get_cached(p, "audio") == "audio text"

    def test_missing_file(self, tmp_path: Path) -> None:
        assert get_cached(tmp_path / "missing", "image") is None
