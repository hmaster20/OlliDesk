"""9.7: add_from_media — kind + thumbnail."""

from __future__ import annotations

from pathlib import Path

from tools.attached_files import AttachedFileStore


class TestAddFromMedia:
    def test_image_with_thumbnail(self, tmp_path: Path) -> None:
        store = AttachedFileStore()
        p = tmp_path / "pic.png"
        p.write_bytes(b"x")
        attached = store.add_from_media(
            p, "OCR text", kind="image", thumbnail="data:image/png;base64,AAA"
        )
        assert attached.kind == "image"
        assert attached.thumbnail.startswith("data:")
        assert attached.content == "OCR text"

    def test_audio_no_thumbnail(self, tmp_path: Path) -> None:
        store = AttachedFileStore()
        p = tmp_path / "a.mp3"
        p.write_bytes(b"x")
        attached = store.add_from_media(p, "transcript", kind="audio")
        assert attached.kind == "audio"
        assert attached.thumbnail == ""

    def test_replaces_previous(self, tmp_path: Path) -> None:
        store = AttachedFileStore()
        p = tmp_path / "a.mp3"
        p.write_bytes(b"x")
        store.add_from_media(p, "old", kind="audio")
        store.add_from_media(p, "new", kind="audio")
        assert len(store) == 1
        assert store.find(str(p)).content == "new"  # type: ignore[union-attr]