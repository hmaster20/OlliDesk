"""Тесты InvestigationState: счётчики для валидации Observation.

`attempted_calls` / `successful_observations` — ключевой инвариант
React.md 2.4: если модель пыталась вызывать инструменты, но все вызовы
упали, AgentLoop не даёт Final Answer (см. _OBSERVATION_NUDGE).
"""

from __future__ import annotations

from agents.investigation import InvestigationState


class TestObservationCounters:
    def test_initial_counters_are_zero(self) -> None:
        state = InvestigationState()
        assert state.attempted_calls == 0
        assert state.successful_observations == 0

    def test_successful_call_increments_both(self) -> None:
        state = InvestigationState()
        state.note_call("read_file", {"path": "a.py"}, "x" * 100, is_error=False)
        assert state.attempted_calls == 1
        assert state.successful_observations == 1

    def test_failed_call_increments_attempted_only(self) -> None:
        state = InvestigationState()
        state.note_call("read_file", {"path": "missing.py"}, "not found", is_error=True)
        assert state.attempted_calls == 1
        assert state.successful_observations == 0

    def test_mixed_calls(self) -> None:
        state = InvestigationState()
        state.note_call("read_file", {"path": "a.py"}, "x" * 100, is_error=False)
        state.note_call("read_file", {"path": "b.py"}, "err", is_error=True)
        state.note_call("list_directory", {"path": "."}, "files", is_error=False)
        assert state.attempted_calls == 3
        assert state.successful_observations == 2

    def test_error_does_not_register_read(self) -> None:
        """Проваленный read_file не попадает в read_files/reads_per_file."""
        state = InvestigationState()
        state.note_call("read_file", {"path": "missing.py"}, "not found", is_error=True)
        assert "missing.py" not in state.read_files
        assert "missing.py" not in state.reads_per_file
        assert state.successful_reads == 0


class TestNoFalsePositiveOnChat:
    """Обычный чат без tool calls не должен триггерить Observation-нудж."""

    def test_no_calls_no_counters(self) -> None:
        state = InvestigationState()
        assert state.attempted_calls == 0
        assert state.successful_observations == 0
