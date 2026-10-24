"""Тесты git-helper'ов (9.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.git_ops import get_git_status_for_file, read_file_at_head


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Создаёт git-репозиторий с одним коммитом и изменённым файлом."""
    try:
        from git import Repo
    except ImportError:
        pytest.skip("GitPython не установлен")

    repo = Repo.init(tmp_path)

    # Начальный коммит.
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "unchanged.py").write_text("y = 2\n", encoding="utf-8")
    repo.index.add(["tracked.py", "unchanged.py"])
    repo.index.commit("initial")

    # Изменяем tracked.py в рабочей копии.
    (tmp_path / "tracked.py").write_text("x = 1\nz = 3\n", encoding="utf-8")
    # Новый (не в HEAD) файл.
    (tmp_path / "new.py").write_text("new = True\n", encoding="utf-8")

    return tmp_path


class TestReadFileAtHead:
    def test_tracked_file_returns_head_content(self, git_repo: Path) -> None:
        content = read_file_at_head(git_repo, git_repo / "tracked.py")
        # В HEAD старая версия — без "z = 3".
        assert content is not None
        assert "x = 1" in content
        assert "z = 3" not in content

    def test_unchanged_file_matches_head(self, git_repo: Path) -> None:
        content = read_file_at_head(git_repo, git_repo / "unchanged.py")
        assert content is not None
        assert "y = 2" in content

    def test_new_file_returns_none(self, git_repo: Path) -> None:
        # new.py не в HEAD — возвращает None.
        content = read_file_at_head(git_repo, git_repo / "new.py")
        assert content is None

    def test_non_git_dir_returns_none(self, tmp_path: Path) -> None:
        file = tmp_path / "f.py"
        file.write_text("hello", encoding="utf-8")
        assert read_file_at_head(tmp_path, file) is None

    def test_relative_path(self, git_repo: Path) -> None:
        content = read_file_at_head(git_repo, "tracked.py")
        assert content is not None
        assert "x = 1" in content


class TestGetGitStatusForFile:
    def test_modified_file(self, git_repo: Path) -> None:
        status = get_git_status_for_file(git_repo, git_repo / "tracked.py")
        assert status is not None
        # " M" (изменён в рабочей копии, не в индексе).
        assert status[0] == " " or status[1] == "M"

    def test_unchanged_file_returns_none(self, git_repo: Path) -> None:
        status = get_git_status_for_file(git_repo, git_repo / "unchanged.py")
        assert status is None

    def test_untracked_file(self, git_repo: Path) -> None:
        status = get_git_status_for_file(git_repo, git_repo / "new.py")
        assert status == "??"

    def test_non_git_dir_returns_none(self, tmp_path: Path) -> None:
        file = tmp_path / "f.py"
        file.write_text("hello", encoding="utf-8")
        assert get_git_status_for_file(tmp_path, file) is None
