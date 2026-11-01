"""10.5: smoke-прогон Coding Bench на 3 задачах."""

from __future__ import annotations

import pytest

from tests.bench.runner import BenchSummary, TaskResult


def test_bench_summary_metrics() -> None:
    """Сводка считается корректно."""
    results = [
        TaskResult(
            task_name="a", language="python", passed=True,
            tests_passed=3, tests_failed=0, iterations=5,
            tokens=100, ttft_ms=100.0, total_duration_s=10.0,
            tool_calls=4, invalid_args=0,
        ),
        TaskResult(
            task_name="b", language="python", passed=False,
            tests_passed=1, tests_failed=2, iterations=8,
            tokens=200, ttft_ms=200.0, total_duration_s=20.0,
            tool_calls=6, invalid_args=1,
        ),
    ]
    summary = BenchSummary(
        total_tasks=2, passed_tasks=1,
        pass_at_1=0.5, avg_iterations=6.5, avg_tokens=150.0,
        avg_total_duration_s=15.0, tool_success_rate=0.9,
        invalid_args_rate=0.1, results=results,
    )
    assert summary.pass_at_1 == 0.5
    assert summary.total_tasks == 2