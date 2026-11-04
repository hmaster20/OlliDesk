"""10.5: smoke-проверка структуры задач Coding Bench."""

from __future__ import annotations

from pathlib import Path

import pytest

TASKS_DIR = Path(__file__).parent / "tasks"


def _task_dirs() -> list[Path]:
    return sorted(
        d for d in TASKS_DIR.iterdir()
        if d.is_dir() and (d / "task.md").exists()
    )


def test_tasks_exist() -> None:
    """Минимум 3 задачи в bench."""
    tasks = _task_dirs()
    assert len(tasks) >= 3, f"найдено {len(tasks)} задач: {[t.name for t in tasks]}"


def test_each_task_has_task_md_and_repo() -> None:
    for task in _task_dirs():
        assert (task / "task.md").is_file(), f"{task.name}: нет task.md"
        assert (task / "repo").is_dir(), f"{task.name}: нет repo/"


def test_task_names_include_language() -> None:
    """Имя задачи должно начинаться с языка (py_/go_/ts_/cs_)."""
    valid_prefixes = ("py_", "go_", "ts_", "cs_")
    for task in _task_dirs():
        assert task.name.startswith(valid_prefixes), (
            f"{task.name}: имя должно начинаться с {valid_prefixes}"
        )


def test_py_fix_parser_structure() -> None:
    """Конкретная задача: правильная структура."""
    task = TASKS_DIR / "py_fix_parser"
    assert task.is_dir()
    assert (task / "repo" / "parser.py").is_file()
    assert (task / "tests" / "test_parser.py").is_file()
    # В задаче должен быть buggy-код, чтобы тест не проходил сразу.
    content = (task / "repo" / "parser.py").read_text(encoding="utf-8")
    assert "parts[:-1]" in content, "py_fix_parser: bug потерян"