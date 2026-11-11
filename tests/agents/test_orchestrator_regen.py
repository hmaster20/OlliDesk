"""Тесты Orchestrator: пропуск regen при исчерпанном бюджете времени.

Регрессия: синтез 7B на CPU занимает ~230 с; регенерация (ещё один полный
синтез) вышибала пайплайн по таймауту 5 минут, и всё исследование
переходило в медленный ReAct-fallback. Теперь при `time_budget_s` orchestrator
сам пропускает regen и отдаёт первый вариант.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from agents.critic import Critique
from agents.model_pool import ModelPool
from agents.orchestrator import Orchestrator
from fs.intel.graph import DependencyGraph, GraphEdge, GraphNode


def _graph() -> DependencyGraph:
    g = DependencyGraph()
    g.add_node(
        GraphNode(
            id="file:app/main.py",
            kind="file",
            name="main.py",
            path="app/main.py",
            metadata={"is_entry": True},
            score=10.0,
        )
    )
    g.add_node(
        GraphNode(id="class:app/main.py::App", kind="class", name="App", path="app/main.py", score=5.0)
    )
    g.add_edge(GraphEdge(source="class:app/main.py::App", target="file:app/main.py", kind="belongs_to"))
    return g


def _fake_intel() -> SimpleNamespace:
    return SimpleNamespace(
        graph=_graph(),
        entry_points=["app/main.py"],
        file_count=200,  # > SMALL_PROJECT_FILES -> light=False, regen разрешён
        total_bytes=1_000_000,
        repomap=SimpleNamespace(as_prompt_block=lambda: "L0-дерево"),
        manifests=[],
        domain={},
        root=".",
    )


class _FakePool:
    def client(self, role):
        return object()

    def runtime_ctx(self, role) -> int:
        return 8192

    def spec(self, role):
        return SimpleNamespace(temperature=0.0)


async def _ok_critique(answer: str, task: str, planner_client) -> Critique:
    return Critique(ok=True, missing_sections=[], format_issues=[], hint="")


@pytest.fixture
def orch(tmp_path, monkeypatch) -> Orchestrator:
    # _FakePool структурно подменяет ModelPool в тестах (client/
    # runtime_ctx/spec). Cast — чтобы не тащить настоящий ModelPool
    # с загрузкой llama.cpp в unit-тест.
    o = Orchestrator(
        pool=cast(ModelPool, _FakePool()),
        intel_cache_dir=tmp_path / "intel",
    )
    fake_intel = _fake_intel()

    async def _classify(q: str):
        return "architecture", "whole_project"

    async def _plan(task, scope, intel):
        return {"focus": [], "depth": 1, "must_read": []}

    async def _collect(task, scope, intel, plan_obj, evidence, ev, *, iterative, light):
        return None

    # Проза с путём вне индекса -> verify падает -> ветка regen активна.
    async def _gen(client, role, op, prompt):
        return "Обзор компонентов. Схема включает ghost/missing.cs как ключевой модуль."

    monkeypatch.setattr(o, "_classify", _classify)
    monkeypatch.setattr(o, "_plan", _plan)
    monkeypatch.setattr(o, "_collect", _collect)
    monkeypatch.setattr(o, "_gen", _gen)
    monkeypatch.setattr(o, "_intel", _make_async(lambda: fake_intel))
    monkeypatch.setattr("agents.orchestrator.critique", _ok_critique)
    return o


def _make_async(fn):
    async def wrapper(*args, **kwargs):
        return fn()

    return wrapper


class TestRegenTimeBudget:
    @pytest.mark.asyncio
    async def test_regen_skipped_when_budget_exhausted(self, orch, tmp_path):
        events: list[tuple[str, dict]] = []
        result = await orch.run(
            "опиши архитектуру проекта",
            project_root=tmp_path,
            history=[],
            on_event=lambda k, d: events.append((k, d)),
            time_budget_s=0.0,  # бюджет исчерпан сразу
        )
        kinds = [k for k, _ in events]
        assert "regen_skipped" in kinds
        assert "regen" not in kinds
        assert result.answer  # первый вариант возвращён как есть
        assert result.verify_ok is False

    @pytest.mark.asyncio
    async def test_regen_runs_without_budget(self, orch, tmp_path):
        calls: list[str] = []

        original_gen = orch._gen

        async def _gen(client, role, op, prompt):
            calls.append(op.value)
            return await original_gen(client, role, op, prompt)

        orch._gen = _gen
        events: list[tuple[str, dict]] = []
        await orch.run(
            "опиши архитектуру проекта",
            project_root=tmp_path,
            history=[],
            on_event=lambda k, d: events.append((k, d)),
            time_budget_s=None,  # без бюджета — regen как прежде
        )
        kinds = [k for k, _ in events]
        assert "regen" in kinds
        assert "regen_skipped" not in kinds
        assert "regen" in calls
