"""10.2: run_tests — парсинг + timeout + sandbox."""

from __future__ import annotations

from pathlib import Path

from tools.test_ops import (
    _parse_dotnet,
    _parse_go,
    _parse_pytest,
    run_tests,
)


class TestParsers:
    def test_pytest_ok(self) -> None:
        out = "===== 3 passed in 0.12s ====="
        res = _parse_pytest(out, duration=0.12, exit_code=0)
        assert res.passed == 3
        assert res.failed == 0
        assert res.ok

    def test_pytest_failure(self) -> None:
        out = (
            "_____ test_foo _____\n"
            "tests/test_x.py:10: AssertionError: nope\n"
            "===== 1 failed, 2 passed in 1.5s =====\n"
        )
        res = _parse_pytest(out, duration=1.5, exit_code=1)
        assert res.failed == 1
        assert res.passed == 2
        assert len(res.failures) == 1
        assert res.failures[0].file == "tests/test_x.py"
        assert res.failures[0].line == 10

    def test_dotnet(self) -> None:
        out = "Failed!  - Failed:     1, Passed:     5, Skipped:     0, Total:     6, Duration: 1 s"
        res = _parse_dotnet(out, duration=1.0, exit_code=1)
        assert res.failed == 1
        assert res.passed == 5

    def test_go(self) -> None:
        out = "ok   example.com/pkg   0.12s\nFAIL example.com/other  0.5s\n"
        res = _parse_go(out, duration=0.62, exit_code=1)
        assert res.passed == 1
        assert res.failed == 1


class TestRunTests:
    def test_no_project_root(self) -> None:
        res = run_tests(framework="pytest", project_root="")
        assert isinstance(res, dict)
        assert res["ok"] is False
        assert "требует открытый проект" in res["_llm_text"]

    def test_target_outside(self, tmp_path: Path) -> None:
        res = run_tests(
            framework="pytest",
            target="../etc/passwd",
            project_root=str(tmp_path),
        )
        assert isinstance(res, dict)
        assert res["ok"] is False
        assert "вне проекта" in res["_llm_text"]

    def test_pytest_success(self, tmp_path: Path) -> None:
        """Реальный pytest-прогон на крошечном проекте."""
        (tmp_path / "test_smoke.py").write_text(
            "def test_ok():\n    assert 1 + 1 == 2\n",
            encoding="utf-8",
        )
        res = run_tests(
            framework="pytest",
            project_root=str(tmp_path),
            timeout_s=60,
        )
        assert isinstance(res, dict)
        assert res["passed"] >= 1
        assert res["ok"] is True
