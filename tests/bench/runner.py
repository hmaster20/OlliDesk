"""10.5: Coding Bench runner — прогон задач через AgentLoop и подсчёт метрик.

Каждая задача — каталог с:
  * `repo/` — исходный код проекта;
  * `tests/` — тесты (не входят в repo, копируются поверх);
  * `task.md` — описание задачи для модели;
  * `expected.patch` (опционально) — эталонный патч (не обязателен).

Раннер:
  1. Копирует `repo/` и `tests/` во временный каталог.
  2. Запускает `AgentLoop` с coder-моделью и `run_tests`/`apply_patch`/…
  3. Прогоняет `run_tests` после завершения.
  4. Пишет результат в `bench_results.jsonl`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class TaskResult:
    """Результат одной задачи."""

    task_name: str
    language: str
    passed: bool
    tests_passed: int
    tests_failed: int
    iterations: int
    tokens: int
    ttft_ms: float | None
    total_duration_s: float
    tool_calls: int
    invalid_args: int
    error: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchSummary:
    """Агрегаты по всем задачам."""

    total_tasks: int
    passed_tasks: int
    pass_at_1: float
    avg_iterations: float
    avg_tokens: float
    avg_total_duration_s: float
    tool_success_rate: float
    invalid_args_rate: float
    results: list[TaskResult]

from agents.ollama_client import ChatMessage
def _make_user(content: str) -> ChatMessage:
    return ChatMessage(role="user", content=content)
async def _run_one_task(
    task_dir: Path,
    *,
    client: Any,
    registry: Any,
    model: str,
    max_iterations: int = 30,
) -> TaskResult:
    """Прогоняет одну задачу через AgentLoop."""
    from agents.agent_loop import AgentContext
    from agents.investigation import InvestigationState  # noqa: F401
    from core.config import AgentMode

    task_name = task_dir.name
    task_md = task_dir / "task.md"
    repo_dir = task_dir / "repo"
    tests_dir = task_dir / "tests"

    if not task_md.exists() or not repo_dir.exists():
        return TaskResult(
            task_name=task_name,
            language="?",
            passed=False,
            tests_passed=0,
            tests_failed=0,
            iterations=0,
            tokens=0,
            ttft_ms=None,
            total_duration_s=0.0,
            tool_calls=0,
            invalid_args=0,
            error="task structure invalid",
        )

    language = _language_for_task(task_name, repo_dir)

    # Копируем repo+tests во временный каталог.
    with tempfile.TemporaryDirectory(prefix="bench_") as tmp:
        work = Path(tmp)
        shutil.copytree(repo_dir, work, dirs_exist_ok=True)
        if tests_dir.exists():
            shutil.copytree(tests_dir, work, dirs_exist_ok=True)

        task_text = task_md.read_text(encoding="utf-8")
        messages = [
            _make_user(f"Задача: {task_text}\n\nТы — coding-агент. "
                       f"Используй apply_patch/run_tests. Работай в этой директории.")
        ]

        from agents.agent_loop import AgentLoop

        loop = AgentLoop(client=client, registry=registry, model=model)
        started = time.perf_counter()
        tool_calls = 0
        invalid_args = 0

        async def _on_tool_start(_name: str, _args: dict) -> None:
            nonlocal tool_calls
            tool_calls += 1

        async def _on_tool_result(_n, _c, is_error: bool, _m) -> None:
            nonlocal invalid_args
            if is_error:
                invalid_args += 1

        ctx = AgentContext(
            project_root=str(work),
            project_name=task_name,
            mode=AgentMode.AGENT,
        )

        try:
            await loop.run(
                context=ctx,
                messages_history=messages,
                on_token=lambda _t: _noop(),
                on_tool_request=lambda _n, _d, _a: _always_approve(),
                max_iterations=max_iterations,
                on_tool_start=_on_tool_start,
                on_tool_result=_on_tool_result,
            )
        except Exception as e:
            return TaskResult(
                task_name=task_name,
                language=language,
                passed=False,
                tests_passed=0,
                tests_failed=0,
                iterations=loop.last_budget_snapshot.get("iterations", 0)
                if loop.last_budget_snapshot
                else 0,
                tokens=0,
                ttft_ms=None,
                total_duration_s=time.perf_counter() - started,
                tool_calls=tool_calls,
                invalid_args=invalid_args,
                error=f"agent: {e}",
            )

        total_s = time.perf_counter() - started

        # Прогон тестов.
        from tools.test_ops import _run_tests_sync

        framework = _framework_for_task(language)
        try:
            test_result = _run_tests_sync(
                framework=framework,
                target=None,
                cwd=work,
                timeout_s=120,
            )
        except Exception as e:
            return TaskResult(
                task_name=task_name,
                language=language,
                passed=False,
                tests_passed=0,
                tests_failed=0,
                iterations=0,
                tokens=0,
                ttft_ms=None,
                total_duration_s=total_s,
                tool_calls=tool_calls,
                invalid_args=invalid_args,
                error=f"tests: {e}",
            )

        stats = getattr(client, "last_stats", {}) or {}
        return TaskResult(
            task_name=task_name,
            language=language,
            passed=test_result.ok,
            tests_passed=test_result.passed,
            tests_failed=test_result.failed + test_result.errors,
            iterations=int(
                (loop.last_budget_snapshot or {}).get("iterations", 0)
            ),
            tokens=int(stats.get("tokens", 0)),
            ttft_ms=float(stats["ttft_ms"]) if stats.get("ttft_ms") else None,
            total_duration_s=total_s,
            tool_calls=tool_calls,
            invalid_args=invalid_args,
        )


def _language_for_task(name: str, repo: Path) -> str:
    if name.startswith("py_"):
        return "python"
    if name.startswith("go_"):
        return "go"
    if name.startswith("ts_"):
        return "typescript"
    if name.startswith("cs_"):
        return "csharp"
    # По расширениям.
    for ext, lang in (
        (".py", "python"),
        (".go", "go"),
        (".ts", "typescript"),
        (".cs", "csharp"),
    ):
        if any(repo.rglob(f"*{ext}")):
            return lang
    return "?"


def _framework_for_task(language: str) -> str:
    return {
        "python": "pytest",
        "go": "go",
        "typescript": "npm",
        "csharp": "dotnet",
    }.get(language, "pytest")


async def _noop() -> None:
    return None


async def _always_approve() -> bool:
    return True


def run_bench(
    tasks_dir: Path,
    *,
    model: str,
    client: Any,
    registry: Any,
    max_iterations: int = 30,
    only: list[str] | None = None,
) -> BenchSummary:
    """Синхронная обёртка: прогоняет все задачи, возвращает сводку."""
    task_dirs = sorted(
        d
        for d in tasks_dir.iterdir()
        if d.is_dir() and (d / "task.md").exists()
    )
    if only:
        wanted = set(only)
        task_dirs = [d for d in task_dirs if d.name in wanted]

    logger.info(f"Coding Bench: {len(task_dirs)} задач")

    results: list[TaskResult] = []
    for task_dir in task_dirs:
        logger.info(f"Coding Bench: {task_dir.name}")
        try:
            r = asyncio.run(
                _run_one_task(
                    task_dir,
                    client=client,
                    registry=registry,
                    model=model,
                    max_iterations=max_iterations,
                )
            )
        except Exception as e:
            logger.exception(f"Coding Bench {task_dir.name} упал")
            r = TaskResult(
                task_name=task_dir.name,
                language="?",
                passed=False,
                tests_passed=0,
                tests_failed=0,
                iterations=0,
                tokens=0,
                ttft_ms=None,
                total_duration_s=0.0,
                tool_calls=0,
                invalid_args=0,
                error=str(e),
            )
        results.append(r)

    passed = sum(1 for r in results if r.passed)
    n = len(results) or 1
    total_tool_calls = sum(r.tool_calls for r in results) or 1
    return BenchSummary(
        total_tasks=len(results),
        passed_tasks=passed,
        pass_at_1=round(passed / n, 3),
        avg_iterations=round(sum(r.iterations for r in results) / n, 1),
        avg_tokens=round(sum(r.tokens for r in results) / n, 1),
        avg_total_duration_s=round(
            sum(r.total_duration_s for r in results) / n, 2
        ),
        tool_success_rate=round(
            (total_tool_calls - sum(r.invalid_args for r in results))
            / total_tool_calls,
            3,
        ),
        invalid_args_rate=round(
            sum(r.invalid_args for r in results) / total_tool_calls, 3
        ),
        results=results,
    )


def save_results(summary: BenchSummary, path: Path) -> None:
    """Сохраняет результаты в JSONL + markdown-сводку."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in summary.results:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    md = path.with_suffix(".md")
    lines = [
        "# Coding Bench",
        "",
        f"- Задач: {summary.total_tasks}",
        f"- Прошло: {summary.passed_tasks}",
        f"- pass@1: {summary.pass_at_1:.2%}",
        f"- avg iterations: {summary.avg_iterations}",
        f"- avg tokens: {summary.avg_tokens}",
        f"- avg duration: {summary.avg_total_duration_s}s",
        f"- tool success rate: {summary.tool_success_rate:.1%}",
        f"- invalid args rate: {summary.invalid_args_rate:.1%}",
        "",
        "| Задача | Язык | Passed | Тесты | Итер. | Токены | Время |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in summary.results:
        lines.append(
            f"| {r.task_name} | {r.language} | "
            f"{'✅' if r.passed else '❌'} | "
            f"{r.tests_passed}/{r.tests_passed + r.tests_failed} | "
            f"{r.iterations} | {r.tokens} | {r.total_duration_s:.1f}s |"
        )
    md.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Coding Bench: {path} + {md}")


__all__ = ["BenchSummary", "TaskResult", "run_bench", "save_results"]