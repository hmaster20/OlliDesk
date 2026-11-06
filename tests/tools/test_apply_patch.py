"""10.1: apply_patch — успех, stale, ошибки."""

from __future__ import annotations

from pathlib import Path

from tools.file_ops import apply_patch


def _basic_patch(old: str, new: str, *, start: int = 1) -> str:
    """Простейший патч для однострочной замены."""
    return f"--- a/file.py\n+++ b/file.py\n@@ -{start},1 +{start},1 @@\n-{old}\n+{new}\n"


class TestApplyPatch:
    def test_basic_replace(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("x = 1\ny = 2\n")
        result = apply_patch(
            path=str(target),
            patch=_basic_patch("x = 1", "x = 42"),
            project_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert target.read_text() == "x = 42\ny = 2\n"
        assert result["lines_added"] == 1
        assert result["lines_removed"] == 1

    def test_add_line(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("a\nb\n")
        patch = "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n a\n+inserted\n b\n"
        result = apply_patch(
            path=str(target),
            patch=patch,
            project_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert target.read_text() == "a\ninserted\nb\n"

    def test_stale_check_hash_mismatch(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("v1")
        result = apply_patch(
            path=str(target),
            patch=_basic_patch("v1", "v2"),
            expected_hash="deadbeef",
            project_root=str(tmp_path),
        )
        assert isinstance(result, str)
        assert "stale check failed" in result
        assert target.read_text() == "v1"  # не записан

    def test_stale_check_success(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("v1\n")
        from tools.git_ops import file_fingerprint

        fp = file_fingerprint(target)
        result = apply_patch(
            path=str(target),
            patch=_basic_patch("v1", "v2"),
            expected_hash=fp["hash"][:8],
            project_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert target.read_text() == "v2\n"

    def test_context_mismatch(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("actual\n")
        result = apply_patch(
            path=str(target),
            patch=_basic_patch("expected", "new"),
            project_root=str(tmp_path),
        )
        assert isinstance(result, str)
        assert "контекст не совпал" in result

    def test_missing_file(self, tmp_path: Path) -> None:
        result = apply_patch(
            path=str(tmp_path / "missing.py"),
            patch=_basic_patch("a", "b"),
            project_root=str(tmp_path),
        )
        assert isinstance(result, str)
        assert "файл не найден" in result

    def test_no_change(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("same\n")
        patch = "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-same\n+same\n"
        result = apply_patch(
            path=str(target),
            patch=patch,
            project_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert result["status"] == "no_change"

    def test_snapshot_created(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("v1\n")
        result = apply_patch(
            path=str(target),
            patch=_basic_patch("v1", "v2"),
            project_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert result["snapshot_id"]

    def test_outside_project_rejected(self, tmp_path: Path) -> None:
        result = apply_patch(
            path=str(tmp_path.parent / "outside.py"),
            patch=_basic_patch("a", "b"),
            project_root=str(tmp_path),
        )
        assert isinstance(result, str)
        assert "вне допустимой директории" in result
