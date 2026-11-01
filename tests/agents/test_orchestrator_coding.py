"""10.7: coding-флоу Orchestrator."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from agents.model_pool import ModelPool
from agents.orchestrator import Orchestrator, RunResult
from fs.intel import FileRecord, ProjectIntel, Symbol
from fs.intel.graph import DependencyGraph, GraphNode


def _minimal_intel(root: Path) -> ProjectIntel:
    """Минимальный ProjectIntel для тестов coding-флоу."""
    from fs.intel.repomap import RepoMap

    g = DependencyGraph()
    g.add_node(
        GraphNode(
            id="file:main.py",
            kind="file",
            name="main.py",
            path="main.py",
            metadata={"is_entry": True},
            score=10.0,
        )
    )
    return ProjectIntel(
        root=str(root),
        file_count=5,
        total_bytes=5000,
        languages={"python": 5},
        manifests=["pyproject.toml"],
        entry_points=["main.py"],
        symbols=[],
        graph=g,
        domain={},
        confidence=0.5,
        repomap=RepoMap(),
    )


class _FakePool:
    def __init__(self):
        self._client = MagicMock()

    def client(self, role):
        return self._client

    def runtime_ctx(self, role) -> int:
        return 8192

    def spec(self, role):
        return SimpleNamespace(temperature=0.35)


@pytest.mark.asyncio
async def test_coding_flow_routes_from_run(tmp_path: Path) -> None:
    """task=write_code → _run_coding_flow."""
    intel = _minimal_intel(tmp_path)
    orch = Orchestrator(
        pool=cast(ModelPool, _FakePool()),
        intel_cache_dir=tmp_path / "intel",
    )

    async def _classify(q):
        return "write_code", "file"

    async def _plan(task, scope, intel):
        return {"focus": [], "depth": 1, "must_read": []}

    async def _gen(client, role, op, prompt):
        return "```diff\n--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-a\n+b\n```"

    monkeypatch_intel = lambda: intel

    orch._classify = _classify  # type: ignore[method-assign]
    orch._gen = _gen  # type: ignore[method-assign]
    orch._intel = lambda root: _async_return(intel)  # type: ignore[method-assign]

    # Заглушки: без реального LSP/apply_patch.
    async def _noop_tests(_root):
        return None

    orch._run_tests_for_coding = _noop_tests  # type: ignore[method-assign]

    events: list[tuple[str, dict]] = []
    result = await orch.run(
        "исправь баг в main.py",
        project_root=tmp_path,
        history=[],
        on_event=lambda k, d: events.append((k, d)),
    )
    assert isinstance(result, RunResult)
    assert result.coding_flow is True
    kinds = [k for k, _ in events]
    assert "coding_plan" in kinds
    assert "coding_iteration" in kinds


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner()