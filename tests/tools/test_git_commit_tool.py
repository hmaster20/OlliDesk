"""9.4: git_commit tool — валидация + sandbox."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    try:
        from git import Repo
    except ImportError:
        pytest.skip("GitPython не установлен")
    repo = Repo.init(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    repo.index.add(["a.py"])
    repo.index.commit("init")
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    return tmp_path


class TestGitCommitTool:
    def test_requires_project_root(self) -> None:
        from tools.git_ops_tool import tool_git_commit

        out = tool_git_commit("feat: x", project_root="")
        assert isinstance(out, str)
        assert "требует открытый проект" in out

    def test_empty_message(self, git_repo: Path) -> None:
        from tools.git_ops_tool import tool_git_commit

        out = tool_git_commit("", project_root=str(git_repo))
        assert isinstance(out, str)
        assert "пустое сообщение" in out

    def test_outside_path_rejected(self, git_repo: Path) -> None:
        from tools.git_ops_tool import tool_git_commit

        out = tool_git_commit(
            "feat: x",
            files=["../../etc/passwd"],
            project_root=str(git_repo),
        )
        assert isinstance(out, str)
        assert "вне проекта" in out

    def test_successful_commit(self, git_repo: Path) -> None:
        from tools.git_ops_tool import tool_git_commit

        out = tool_git_commit(
            "fix: update a.py",
            files=["a.py"],
            project_root=str(git_repo),
        )
        assert isinstance(out, dict)
        assert out["commit_hash"]
        assert "fix: update a.py" in out["_llm_text"]
