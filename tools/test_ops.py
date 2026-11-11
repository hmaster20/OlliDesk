"""10.2: запуск тестов проекта и парсинг результата.

Поддерживаемые фреймворки:
  * **pytest** (`python -m pytest`) — Python.
  * **unittest** (`python -m unittest`) — Python.
  * **dotnet** (`dotnet test`) — C# / .NET.
  * **go** (`go test ./...`) — Go.
  * **npm** (`npm test`) — JS/TS.

Парсеры устроены пессимистично: если не удалось распарсить вывод —
возвращаем сырой stdout/stderr и ``summary="?"``. Модель обязана уметь
работать с fallback — «увидеть ошибку и исправить» важнее, чем точная
статистика.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from core.sandbox import SandboxedPathResolver

# Сколько символов stdout/stderr показывать модели. Больше — режется,
# полный вывод в лог.
MAX_OUTPUT_CHARS = 6000

# Таймаут по умолчанию на одну команду тестов.
DEFAULT_TIMEOUT_S = 180

Framework = Literal["pytest", "unittest", "dotnet", "go", "npm"]


@dataclass
class TestFailure:
    """Одна упавшая проверка/тест."""

    test: str  # имя теста / тестового класса
    message: str  # краткое сообщение об ошибке
    file: str = ""  # путь (если удалось извлечь)
    line: int = 0  # строка (0 — не удалось)


@dataclass
class TestResult:
    """Структурированный результат прогона."""

    framework: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[TestFailure] = field(default_factory=list)
    duration_s: float = 0.0
    exit_code: int = 0
    truncated: bool = False
    raw_output: str = ""

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.errors == 0 and self.exit_code == 0

    def summary_line(self) -> str:
        """Короткая сводка: `12 passed, 1 failed, 0 errors, 0 skipped (3.4s)`."""
        parts = [f"{self.passed} passed"]
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.errors:
            parts.append(f"{self.errors} errors")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        return f"{', '.join(parts)} ({self.duration_s:.1f}s)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "failures": [asdict(f) for f in self.failures],
            "duration_s": self.duration_s,
            "exit_code": self.exit_code,
            "ok": self.ok,
        }


# ---------------------------------------------------------------------------
# Парсеры
# ---------------------------------------------------------------------------

_PYTEST_SUMMARY_RE = re.compile(r"^=+ (?P<parts>[^=]+) in (?P<dur>[\d.]+)s.*?=+$", re.MULTILINE)
_PYTEST_COUNT_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")
_PYTEST_FAIL_HEADER_RE = re.compile(r"^_{5,} (.+?) _{5,}$", re.MULTILINE)
_PYTEST_LOCATION_RE = re.compile(r"^(?P<file>[^\s:]+\.py):(?P<line>\d+):")


def _parse_pytest(output: str, duration: float, exit_code: int) -> TestResult:
    res = TestResult(framework="pytest", duration_s=duration, exit_code=exit_code)
    for m in _PYTEST_SUMMARY_RE.finditer(output):
        for count_m in _PYTEST_COUNT_RE.finditer(m.group("parts")):
            n = int(count_m.group(1))
            kind = count_m.group(2)
            if kind == "passed":
                res.passed += n
            elif kind == "failed":
                res.failed += n
            elif kind.startswith("error"):
                res.errors += n
            elif kind == "skipped":
                res.skipped += n

    # Парсим блоки падений: `_ test_name _` + следующая строка с локейшеном.
    headers = list(_PYTEST_FAIL_HEADER_RE.finditer(output))
    for i, h in enumerate(headers[:20]):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(output)
        block = output[h.end() : end].strip()
        loc_m = _PYTEST_LOCATION_RE.search(block)
        file_path = ""
        line = 0
        if loc_m:
            file_path = loc_m.group("file")
            line = int(loc_m.group("line"))
        # Первая строка после локейшена — сообщение.
        message = ""
        for ln in block.splitlines():
            s = ln.strip()
            if s and not s.startswith("_") and "Error" in s:
                message = s[:300]
                break
        if not message:
            message = block.splitlines()[0][:300] if block else ""
        res.failures.append(
            TestFailure(
                test=h.group(1).strip()[:200],
                message=message,
                file=file_path,
                line=line,
            )
        )
    return res


_DOTNET_RE = re.compile(
    r"(?:Failed!|Passed!)\s*-\s*Failed:\s*(\d+),\s*Passed:\s*(\d+),"
    r"\s*Skipped:\s*(\d+),\s*Total:\s*(\d+)"
)
_DOTNET_FAIL_RE = re.compile(r"^\s*Failed\s+(?P<name>[^\s]+).*?\[(?P<dur>[\d.]+)\s*(?:ms|s)\]", re.MULTILINE)


def _parse_dotnet(output: str, duration: float, exit_code: int) -> TestResult:
    res = TestResult(framework="dotnet", duration_s=duration, exit_code=exit_code)
    m = _DOTNET_RE.search(output)
    if m:
        failed, passed, skipped, _total = (int(g) for g in m.groups())
        res.failed = failed
        res.passed = passed
        res.skipped = skipped
    for fm in _DOTNET_FAIL_RE.finditer(output):
        res.failures.append(TestFailure(test=fm.group("name")[:200], message=""))
        if len(res.failures) >= 20:
            break
    return res


_GO_RE = re.compile(
    r"^(?P<status>ok|FAIL|---\s+FAIL:)\s+(?P<name>\S+)(?:\s+(?P<dur>[\d.]+s))?",
    re.MULTILINE,
)


def _parse_go(output: str, duration: float, exit_code: int) -> TestResult:
    res = TestResult(framework="go", duration_s=duration, exit_code=exit_code)
    for m in _GO_RE.finditer(output):
        status = m.group("status")
        name = m.group("name")
        if status == "ok":
            res.passed += 1
        elif status == "FAIL":
            res.failed += 1
            res.failures.append(TestFailure(test=name[:200], message=""))
        elif "--- FAIL" in status:
            # Отдельный упавший тест — учтём его имя отдельно.
            res.failures.append(TestFailure(test=name[:200], message=""))
            if len(res.failures) > 20:
                break
    return res


_NPM_RE = re.compile(r"^(?:Tests:|#\s*tests)\s+(\d+).*?(?:fail\s+(\d+))?", re.MULTILINE)
_NPM_FAILED_RE = re.compile(r"^\s*✕\s+(.+)$", re.MULTILINE)


def _parse_npm(output: str, duration: float, exit_code: int) -> TestResult:
    res = TestResult(framework="npm", duration_s=duration, exit_code=exit_code)
    # Vitest / Jest / Mocha — разные форматы.
    counts = re.search(r"Tests:\s*(?:\d+\s+failed,\s*)?(\d+)\s+passed", output)
    if counts:
        res.passed = int(counts.group(1))
    fails = re.search(r"Tests:\s*(\d+)\s+failed", output)
    if fails:
        res.failed = int(fails.group(1))
    for m in _NPM_FAILED_RE.finditer(output):
        res.failures.append(TestFailure(test=m.group(1).strip()[:200], message=""))
        if len(res.failures) >= 20:
            break
    return res


_PARSERS = {
    "pytest": _parse_pytest,
    "unittest": _parse_pytest,  # unittest выводит в похожем формате
    "dotnet": _parse_dotnet,
    "go": _parse_go,
    "npm": _parse_npm,
}


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------


def _build_command(framework: Framework, target: str | None) -> tuple[list[str], str]:
    """Возвращает (команда, shell_or_none).

    Для ``npm`` нужен shell на Windows (npm — это .cmd).
    """
    if framework == "pytest":
        cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short"]
        if target:
            cmd.append(target)
        return cmd, ""
    if framework == "unittest":
        cmd = [sys.executable, "-m", "unittest"]
        if target:
            cmd.extend(["discover", "-s", target])
        return cmd, ""
    if framework == "dotnet":
        cmd = ["dotnet", "test", "--nologo", "--verbosity=minimal"]
        if target:
            cmd.extend(["--filter", target])
        return cmd, ""
    if framework == "go":
        cmd = ["go", "test", "./..."]
        if target:
            cmd = ["go", "test", target]
        return cmd, ""
    if framework == "npm":
        cmd = ["npm", "test", "--silent"]
        return cmd, ""
    raise ValueError(f"Unknown framework: {framework}")


def _run_tests_sync(
    *,
    framework: Framework,
    target: str | None,
    cwd: Path,
    timeout_s: int,
) -> TestResult:
    cmd, shell = _build_command(framework, target)
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    started = time.perf_counter()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            timeout=timeout_s,
            creationflags=creationflags,
            shell=bool(shell),
        )
    except subprocess.TimeoutExpired as e:
        elapsed = time.perf_counter() - started
        partial = e.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return TestResult(
            framework=framework,
            duration_s=elapsed,
            exit_code=-1,
            errors=1,
            truncated=True,
            raw_output=(partial or "")[:MAX_OUTPUT_CHARS],
        )
    except FileNotFoundError as e:
        return TestResult(
            framework=framework,
            duration_s=time.perf_counter() - started,
            exit_code=-2,
            errors=1,
            raw_output=f"Не найден исполняемый файл: {e}",
        )

    elapsed = time.perf_counter() - started
    raw = (result.stdout or "") + (result.stderr or "")
    truncated = len(raw) > MAX_OUTPUT_CHARS
    parsed = _PARSERS[framework](raw, elapsed, result.returncode)
    parsed.truncated = truncated
    parsed.raw_output = raw[:MAX_OUTPUT_CHARS]
    return parsed


@tool(
    name="run_tests",
    description=(
        "Запускает тесты проекта и возвращает структурированный результат "
        "(passed/failed/errors/failures с jump-to-line).\n\n"
        "Поддерживаемые фреймворки: pytest, unittest, dotnet, go, npm.\n"
        "После правки `.py`/`.cs`/`.go`/`.ts` — ОБЯЗАТЕЛЬНО запусти run_tests. "
        "Если тесты упали — исправь и повтори (не более 3 раз).\n\n"
        "Примеры:\n"
        "  run_tests(framework='pytest', target='tests/')\n"
        "  run_tests(framework='dotnet')\n"
        "  run_tests(framework='go')"
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
    is_mutating=False,  # не пишет в проект (но может создать .pytest_cache/)
    category="shell",
    capabilities=["shell"],
    timeout_s=0.0,  # управляется параметром timeout_s внутри
)
def run_tests(
    framework: Framework,
    target: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    project_root: str = "",
) -> dict[str, Any]:
    """Запускает тесты проекта.

    Args:
        framework: pytest / unittest / dotnet / go / npm.
        target: Ограничить прогон (файл/каталог/фильтр). ``None`` — всё.
        timeout_s: Таймаут на прогон (1..600).
        project_root: Корень проекта.

    Returns:
        dict с ``_llm_text`` (что видит модель) + структурированные поля
        (passed/failed/errors/failures) для UI.
    """
    if not project_root:
        return {
            "_llm_text": "Ошибка: run_tests требует открытый проект.",
            "ok": False,
            "error": "no_project",
        }
    resolver = SandboxedPathResolver(project_root)
    if resolver.root is None:
        return {
            "_llm_text": "Ошибка: некорректный project_root.",
            "ok": False,
            "error": "bad_project_root",
        }
    if target:
        target_path = resolver.resolve(target)
        if target_path is None:
            return {
                "_llm_text": (f"Ошибка: target '{target}' вне проекта или в служебной директории."),
                "ok": False,
                "error": "target_outside",
            }
        target = str(target_path)

    try:
        t = int(timeout_s)
    except (TypeError, ValueError):
        t = DEFAULT_TIMEOUT_S
    t = max(1, min(t, 600))

    logger.info(f"run_tests: framework={framework}, target={target!r}, cwd={resolver.root}, timeout={t}s")
    result = _run_tests_sync(
        framework=framework,
        target=target,
        cwd=resolver.root,
        timeout_s=t,
    )

    # Формируем текст для LLM.
    lines: list[str] = []
    header = f"$ {framework}" + (f" {Path(target).name}" if target else "") + f"  →  {result.summary_line()}"
    lines.append(header)
    if result.truncated:
        lines.append("[Вывод обрезан]")
    if result.failures:
        lines.append("")
        lines.append("Провалы:")
        for f in result.failures[:10]:
            loc = f" ({f.file}:{f.line})" if f.file else ""
            lines.append(f"  ✗ {f.test}{loc}")
            if f.message:
                lines.append(f"      {f.message[:200]}")
        if len(result.failures) > 10:
            lines.append(f"  … ещё {len(result.failures) - 10}")
    if result.exit_code != 0 and not result.failures:
        lines.append("")
        lines.append("Сырой вывод (последние строки):")
        for ln in result.raw_output.splitlines()[-15:]:
            lines.append(f"  {ln}")

    return {
        "_llm_text": "\n".join(lines),
        "ok": result.ok,
        "framework": result.framework,
        "passed": result.passed,
        "failed": result.failed,
        "errors": result.errors,
        "skipped": result.skipped,
        "failures": [asdict(f) for f in result.failures],
        "duration_s": round(result.duration_s, 2),
        "exit_code": result.exit_code,
    }


__all__ = ["run_tests", "TestResult", "TestFailure"]
