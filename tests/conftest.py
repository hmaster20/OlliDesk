from pathlib import Path

import pytest


@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Creates a temporary project for tests."""
    project = tmp_path / "test_project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "main.py").write_text("print('hello')")
    (project / "tests").mkdir()
    (project / "tests" / "test_main.py").write_text("def test_main(): pass")
    return project
