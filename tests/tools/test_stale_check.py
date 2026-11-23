"""10.9: stale check + snapshot."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.git_ops import (
    create_file_snapshot,
    file_fingerprint,
    load_snapshot_meta,
    restore_snapshot,
)


class TestFingerprint:
    def test_existing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "x.txt"
        p.write_text("hello")
        fp = file_fingerprint(p)
        assert fp["hash"]
        assert len(fp["hash"]) == 64
        assert fp["size"] == 5
        assert fp["mtime"] > 0

    def test_missing_file(self, tmp_path: Path) -> None:
        fp = file_fingerprint(tmp_path / "missing")
        assert fp["hash"] == ""
        assert fp["size"] == 0


class TestSnapshot:
    def test_create_and_restore(self, tmp_path: Path) -> None:
        p = tmp_path / "a.txt"
        p.write_text("original")
        sid = create_file_snapshot([p], tool="test")
        assert sid
        # Меняем файл.
        p.write_text("modified")
        # Откат.
        restored = restore_snapshot(sid)
        assert str(p) in restored
        assert p.read_text() == "original"

    def test_snapshot_new_file_deletes_on_restore(self, tmp_path: Path) -> None:
        p = tmp_path / "new.txt"
        sid = create_file_snapshot([p], tool="test")
        p.write_text("created")
        restored = restore_snapshot(sid)
        assert str(p) in restored
        assert not p.exists()

    def test_meta_saved(self, tmp_path: Path) -> None:
        p = tmp_path / "x.txt"
        p.write_text("x")
        sid = create_file_snapshot(
            [p],
            tool="write_file",
            args_summary="x.txt",
            project_root=tmp_path,
        )
        meta = load_snapshot_meta(sid)
        assert meta is not None
        assert meta["tool"] == "write_file"
        assert meta["project_root"] == str(tmp_path)
        assert len(meta["files"]) == 1

    def test_restore_missing_snapshot_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            restore_snapshot("nonexistent")
