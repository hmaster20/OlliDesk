"""9.4: git_context — branch/hash/diff/commits."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Создаёт git-репозиторий с одним коммитом и изменённым файлом."""
    try:
        from git import Repo
    except ImportError:
        pytest.skip("GitPython не установлен")

    repo = Repo.init(tmp_path)
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("y = 2\n", encoding="utf-8")
    repo.index.add(["tracked.py", "other.py"])
    repo.index.commit("initial")
    (tmp_path / "tracked.py").write_text("x = 1\nz = 3\n", encoding="utf-8")
    return tmp_path


class TestBranch:
    def test_get_current_branch(self, git_repo: Path) -> None:
        from tools.git_ops import get_current_branch

        branch = get_current_branch(git_repo)
        assert branch in ("master", "main")

    def test_get_branch_hash(self, git_repo: Path) -> None:
        from tools.git_ops import get_branch_hash

        h = get_branch_hash(git_repo)
        assert 7 <= len(h) <= 40
        assert all(c in "0123456789abcdef" for c in h)

    def test_non_git_returns_empty(self, tmp_path: Path) -> None:
        from tools.git_ops import get_branch_hash, get_current_branch

        assert get_current_branch(tmp_path) == ""
        assert get_branch_hash(tmp_path) == ""


class TestDiff:
    def test_uncommitted_diff(self, git_repo: Path) -> None:
        from tools.git_ops import get_uncommitted_diff

        diff = get_uncommitted_diff(git_repo)
        assert "z = 3" in diff
        assert "tracked.py" in diff

    def test_clean_worktree_empty(self, tmp_path: Path) -> None:
        from git import Repo

        from tools.git_ops import get_uncommitted_diff

        repo = Repo.init(tmp_path)
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        repo.index.add(["a.py"])
        repo.index.commit("init")
        assert get_uncommitted_diff(tmp_path) == ""

    def test_include_untracked(self, git_repo: Path) -> None:
        from tools.git_ops import get_uncommitted_diff

        (git_repo / "new.py").write_text("new\n", encoding="utf-8")
        diff = get_uncommitted_diff(git_repo, include_untracked=True)
        assert "new.py" in diff


class TestCommits:
    def test_get_recent_commits(self, git_repo: Path) -> None:
        from tools.git_ops import get_recent_commits

        commits = get_recent_commits(git_repo, limit=5)
        assert len(commits) >= 1
        c = commits[0]
        assert "hash" in c
        assert "author" in c
        assert "date" in c
        assert "subject" in c
        assert c["subject"] == "initial"

    def test_limit_capped(self, git_repo: Path) -> None:
        from tools.git_ops import get_recent_commits

        commits = get_recent_commits(git_repo, limit=100)
        assert len(commits) <= 50


class TestCommit:
    def test_commit_files(self, git_repo: Path) -> None:
        from tools.git_ops import commit

        result = commit(git_repo, "feat: add z", files=["tracked.py"])
        assert result["ok"] is True
        assert result["commit_hash"]
        assert "tracked.py" in result["files_committed"]

    def test_commit_empty_message(self, git_repo: Path) -> None:
        from tools.git_ops import commit

        result = commit(git_repo, "")
        assert result["ok"] is False
        assert "пустое" in result["error"]

    def test_commit_non_git(self, tmp_path: Path) -> None:
        from tools.git_ops import commit

        result = commit(tmp_path, "test")
        assert result["ok"] is False
        assert "не git" in result["error"]

    def test_commit_nothing_to_commit(self, git_repo: Path) -> None:
        """Повторный коммит без изменений → ошибка."""
        from tools.git_ops import commit

        commit(git_repo, "first", files=["tracked.py"])
        result = commit(git_repo, "second", files=["tracked.py"])
        assert result["ok"] is False  # нечего коммитить
