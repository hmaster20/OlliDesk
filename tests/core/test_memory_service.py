"""4.3: MemoryService — remember/recall/forget, FTS5, preferences."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.services.memory_service import (
    Decision,
    MemoryService,
    Reflection,
)


@pytest.fixture
def svc(tmp_path: Path) -> MemoryService:
    service = MemoryService(tmp_path / "memory.db")
    yield service
    service.close()


def _reflection(
    summary: str = "test",
    *,
    task_type: str = "code",
    outcome: str = "success",
    tags: list[str] | None = None,
    project_path: str = "",
) -> Reflection:
    return Reflection(
        id=None,
        created_at="",
        project_path=project_path,
        task_type=task_type,  # type: ignore[arg-type]
        summary=summary,
        outcome=outcome,  # type: ignore[arg-type]
        tags=tags or [],
    )


class TestReflection:
    def test_remember_and_list(self, svc: MemoryService) -> None:
        rid = svc.remember(_reflection("fixed the parser"))
        assert rid > 0
        recent = svc.list_recent(limit=10)
        assert len(recent) == 1
        assert recent[0].summary == "fixed the parser"
        assert recent[0].id == rid

    def test_remember_empty_summary_raises(self, svc: MemoryService) -> None:
        with pytest.raises(ValueError, match="summary пуст"):
            svc.remember(_reflection(""))

    def test_remember_truncates_long_summary(self, svc: MemoryService) -> None:
        long_text = "x" * 10_000
        svc.remember(_reflection(long_text))
        recent = svc.list_recent(limit=1)
        assert len(recent[0].summary) <= 4000

    def test_forget_removes(self, svc: MemoryService) -> None:
        rid = svc.remember(_reflection("a"))
        assert svc.forget(rid) is True
        assert svc.list_recent(limit=10) == []
        assert svc.forget(rid) is False  # уже удалено

    def test_list_recent_respects_limit(self, svc: MemoryService) -> None:
        for i in range(5):
            svc.remember(_reflection(f"task {i}"))
        recent = svc.list_recent(limit=3)
        assert len(recent) == 3
        # Свежие первыми.
        assert recent[0].summary == "task 4"

    def test_list_recent_project_filter(self, svc: MemoryService) -> None:
        svc.remember(_reflection("a", project_path="p1"))
        svc.remember(_reflection("b", project_path="p2"))
        only_p1 = svc.list_recent(limit=10, project_path="p1")
        assert len(only_p1) == 1
        assert only_p1[0].summary == "a"


class TestRecall:
    def test_recall_finds_relevant(self, svc: MemoryService) -> None:
        svc.remember(_reflection("fixed the parser bug in main.py"))
        svc.remember(_reflection("wrote documentation for API"))
        result = svc.recall("parser bug", top_k=3)
        assert result.hit is True
        assert any("parser" in r.summary for r in result.reflections)

    def test_recall_empty_query(self, svc: MemoryService) -> None:
        svc.remember(_reflection("a"))
        result = svc.recall("", top_k=3)
        assert result.hit is False
        assert result.reflections == []

    def test_recall_short_query_filtered(self, svc: MemoryService) -> None:
        # Запрос короче 3 символов не ищется.
        svc.remember(_reflection("a"))
        result = svc.recall("ab", top_k=3)
        assert result.hit is False

    def test_recall_project_filter(self, svc: MemoryService) -> None:
        svc.remember(_reflection("bug in p1", project_path="p1"))
        svc.remember(_reflection("bug in p2", project_path="p2"))
        result = svc.recall("bug", top_k=5, project_path="p1")
        assert result.hit is True
        assert all(r.project_path == "p1" for r in result.reflections)

    def test_render_for_prompt(self, svc: MemoryService) -> None:
        svc.remember(_reflection("first reflection"))
        result = svc.recall("reflection", top_k=3)
        rendered = result.render_for_prompt(max_chars=1000)
        assert "[M1]" in rendered
        assert "first reflection" in rendered


class TestDecisions:
    def test_remember_and_list(self, svc: MemoryService) -> None:
        did = svc.remember_decision(
            Decision(
                id=None,
                created_at="",
                project_path="p",
                decision="use apply_patch instead of write_file",
                rationale="files exist, context matches",
            )
        )
        assert did > 0
        decisions = svc.list_decisions(limit=10)
        assert len(decisions) == 1
        assert "apply_patch" in decisions[0].decision


class TestPreferences:
    def test_set_get(self, svc: MemoryService) -> None:
        svc.set_preference("theme", "dark")
        assert svc.get_preference("theme") == "dark"
        assert svc.get_preference("missing", "default") == "default"

    def test_overwrite(self, svc: MemoryService) -> None:
        svc.set_preference("theme", "dark")
        svc.set_preference("theme", "light")
        assert svc.get_preference("theme") == "light"

    def test_list(self, svc: MemoryService) -> None:
        svc.set_preference("a", "1")
        svc.set_preference("b", "2")
        prefs = svc.list_preferences()
        assert {p.key for p in prefs} == {"a", "b"}


class TestClear:
    def test_clear_keeps_preferences(self, svc: MemoryService) -> None:
        svc.remember(_reflection("a"))
        svc.set_preference("k", "v")
        svc.clear(keep_preferences=True)
        assert svc.list_recent(limit=10) == []
        assert svc.get_preference("k") == "v"

    def test_clear_all(self, svc: MemoryService) -> None:
        svc.set_preference("k", "v")
        svc.clear(keep_preferences=False)
        assert svc.get_preference("k") is None


class TestExport:
    def test_export_json(self, svc: MemoryService, tmp_path: Path) -> None:
        svc.remember(_reflection("x"))
        svc.set_preference("k", "v")
        out = tmp_path / "export.json"
        svc.export_json(out)
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        assert "reflections" in text
        assert "preferences" in text


class TestCount:
    def test_counts(self, svc: MemoryService) -> None:
        svc.remember(_reflection("a"))
        svc.remember(_reflection("b"))
        svc.set_preference("k", "v")
        counts = svc.count()
        assert counts["reflections"] == 2
        assert counts["decisions"] == 0
        assert counts["preferences"] == 1