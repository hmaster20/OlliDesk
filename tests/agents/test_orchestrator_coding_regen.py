"""10.7: регенерация прозы в coding-флоу Orchestrator."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.critic import Critique
from agents.orchestrator import Orchestrator, RunResult
from fs.intel import ProjectIntel
from fs.intel.graph import DependencyGraph, GraphNode
from fs.intel.repomap import RepoMap


def _minimal_intel(root: Path) -> ProjectIntel:
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
        file_count=3,
        total_bytes=2000,
        languages={"python": 3},
        manifests=[],
        entry_points=["main.py"],
        symbols=[],
        graph=g,
        domain={},
        confidence=0.5,
        repomap=RepoMap(),
    )


class _FakePool:
    def __init__(self):
        self._client = _NoopGenClient()

    def client(self, role):
        return self._client

    def runtime_ctx(self, role) -> int:
        return 8192

    def spec(self, role):
        return SimpleNamespace(temperature=0.35)


class _NoopGenClient:
    """Отдаёт ровно один текст — для `_gen` (это не stream)."""

    async def generate(self, prompt, **kwargs):
        # Первый ответ — с невалидным путём; второй — без.
        if "НЕ прошёл верификацию" in prompt:
            return "Исправленный ответ без ghost."
        return "Исходный ответ: см. ghost/missing.py."


class _TestOrchestrator(Orchestrator):
    """Оркестратор с подменённым _gen (чтобы не гонять модель)."""

    def __init__(self, pool, intel):
        super().__init__(pool=pool)
        self._test_intel = intel
        self._gen_calls = 0
        self._patch_calls = 0

    async def _intel(self, root):  # type: ignore[override]
        return self._test_intel

    async def _gen(self, client, role, op, prompt):  # type: ignore[override]
        self._gen_calls += 1
        if op.value == "regen":
            return "Исправленный ответ без ghost."
        return (
            "```diff\n--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-a\n+b\n```\n\n"
            "Объяснение: см. ghost/missing.py."
        )

    async def _apply_patches(self, patches, project_root, ev):
        self._patch_calls += 1
        return [{"path": p["path"], "status": "ok"} for p in patches]

    async def _run_tests_for_coding(self, root):
        from tools.test_ops import TestResult

        return TestResult(framework="pytest", passed=2, failed=0, errors=0)


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("x = 1\n")
    return tmp_path


@pytest.mark.asyncio
async def test_coding_regen_on_verifier_fail(tmp_project: Path) -> None:
    """Тесты зелёные + verifier fail → регенерация прозы."""
    intel = _minimal_intel(tmp_project)
    pool = _FakePool()
    orch = _TestOrchestrator(pool, intel)

    # Мокаем critique.
    async def _ok_critique(answer, task, planner):
        return Critique(True, [], [], "")

    import agents.orchestrator as _orch_mod

    original = _orch_mod.critique
    _orch_mod.critique = _ok_critique
    try:
        result = await orch._run_coding_flow(
            "исправь main.py",
            task="write_code",
            project_root=tmp_project,
            intel=intel,
            history=[],
            on_event=None,
            quality_mode=False,
            time_budget_s=None,
        )
    finally:
        _orch_mod.critique = original

    assert isinstance(result, RunResult)
    # _gen вызывался: первая итерация + регенерация.
    assert orch._gen_calls >= 2
    # Ответ не содержит ghost (второй ответ лучше).
    assert "ghost" not in result.answer


@pytest.mark.asyncio
async def test_coding_honest_marker_on_tests_fail(tmp_project: Path) -> None:
    """Тесты упали — в ответе явная пометка, регенерации нет."""
    intel = _minimal_intel(tmp_project)
    pool = _FakePool()
    orch = _TestOrchestrator(pool, intel)

    # Переопределяем _run_tests_for_coding: тесты падают.
    async def _fail_tests(root):
        from tools.test_ops import TestResult

        return TestResult(
            framework="pytest",
            passed=1,
            failed=2,
            errors=0,
            exit_code=1,
        )

    orch._run_tests_for_coding = _fail_tests  # type: ignore[method-assign]

    async def _ok_critique(answer, task, planner):
        return Critique(True, [], [], "")

    import agents.orchestrator as _orch_mod

    original = _orch_mod.critique
    _orch_mod.critique = _ok_critique
    try:
        result = await orch._run_coding_flow(
            "исправь main.py",
            task="write_code",
            project_root=tmp_project,
            intel=intel,
            history=[],
            on_event=None,
            quality_mode=False,
            time_budget_s=None,
        )
    finally:
        _orch_mod.critique = original

    assert "Тесты не зелёные" in result.answer
    assert result.tests_failed == 2
