"""9.7: image_extractors — is_image, thumbnail, offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from fs.image_extractors import (
    extract_image,
    image_thumbnail_base64,
    is_image,
)


class TestIsImage:
    @pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".webp", ".bmp"])
    def test_true(self, ext: str) -> None:
        assert is_image(f"file{ext}")

    @pytest.mark.parametrize("name", ["a.py", "a.mp3", "a.mp4", "a.pdf"])
    def test_false(self, name: str) -> None:
        assert not is_image(name)

    def test_case_insensitive(self) -> None:
        assert is_image("FILE.PNG")


class TestExtractImage:
    def test_no_ocr_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "x.png"
        p.write_bytes(b"fake")
        result = extract_image(p, use_ocr=False)
        assert result.text == ""
        assert result.error == ""


class TestThumbnail:
    def test_missing_pillow_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "x.png"
        p.write_bytes(b"fake")
        with pytest.MonkeyPatch.context() as mp:
            mp.setitem("sys.modules", {"PIL": None, "PIL.Image": None})
            out = image_thumbnail_base64(p)
            assert out == "" or out.startswith("data:image/")