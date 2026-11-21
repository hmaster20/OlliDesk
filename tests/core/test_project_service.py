"""2.8: ProjectService — intel cache + git."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.config import AgentMode
from core.services.project_service import ProjectService


class TestIntelCache:
    def test_cache_hit(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1\n")
        svc = ProjectService(intel_ttl_s=60.0)
        with patch("core.services.project_service.analyze_project") as mock_analyze:
            from fs.intel import ProjectIntel
            from fs.intel.graph import DependencyGraph
            from fs.intel.repomap import RepoMap

            fake = ProjectIntel(
                root=str(tmp_path),
                file_count=1,
                total_bytes=5,
                languages={"python": 1},
                manifests=[],
                entry_points=["main.py"],
                symbols=[],
                graph=DependencyGraph(),
                domain={},
                confidence=0.5,
                repomap=RepoMap(),
            )
            mock_analyze.return_value = fake
            r1 = svc.get_intel(tmp_path)
            r2 = svc.get_intel(tmp_path)
            # Второй вызов — из кэша.
            assert mock_analyze.call_count == 1
            assert r1 is r2

    def test_force_ignores_cache(self, tmp_path: Path) -> None:
        svc = ProjectService(intel_ttl_s=60.0)
        with patch("core.services.project_service.analyze_project") as mock_analyze:
            from fs.intel import ProjectIntel
            from fs.intel.graph import DependencyGraph
            from fs.intel.repomap import RepoMap

            fake = ProjectIntel(
                root=str(tmp_path),
                file_count=0,
                total_bytes=0,
                languages={},
                manifests=[],
                entry_points=[],
                symbols=[],
                graph=DependencyGraph(),
                domain={},
                confidence=0.0,
                repomap=RepoMap(),
            )
            mock_analyze.return_value = fake
            svc.get_intel(tmp_path)
            svc.get_intel(tmp_path, force=True)
            assert mock_analyze.call_count == 2


class TestGitContext:
    def test_non_git_returns_empty(self, tmp_path: Path) -> None:
        svc = ProjectService()
        assert svc.build_git_context(tmp_path, mode=AgentMode.AGENT) == ""

    def test_chat_mode_returns_empty(self, tmp_path: Path) -> None:
        svc = ProjectService()
        assert svc.build_git_context(tmp_path, mode=AgentMode.CHAT) == ""
