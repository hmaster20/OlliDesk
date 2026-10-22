"""Тесты diff-режима write_file (9.6.1)."""

from __future__ import annotations

from pathlib import Path

from tools.file_ops import write_file


class TestWriteFileDiff:
    def test_new_file_returns_dict(self, tmp_path: Path) -> None:
        target = tmp_path / "new.py"
        result = write_file(
            path=str(target),
            content="print('hello')\n",
            project_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert result["is_new_file"] is True
        assert result["lines_added"] == 1
        assert result["lines_removed"] == 0
        assert result["syntax_error"] == ""
        assert "+print('hello')" in result["diff"]
        assert result["_llm_text"].startswith("Файл создан")

    def test_update_file_returns_diff(self, tmp_path: Path) -> None:
        target = tmp_path / "existing.py"
        target.write_text("a = 1\nb = 2\n", encoding="utf-8")
        result = write_file(
            path=str(target),
            content="a = 1\nb = 3\nc = 4\n",
            project_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert result["is_new_file"] is False
        assert result["original_content"] == "a = 1\nb = 2\n"
        assert result["lines_added"] == 2
        assert result["lines_removed"] == 1
        # diff должен содержать изменения.
        assert "-b = 2" in result["diff"]
        assert "+b = 3" in result["diff"]
        assert "+c = 4" in result["diff"]

    def test_no_change(self, tmp_path: Path) -> None:
        target = tmp_path / "same.txt"
        target.write_text("hello\n", encoding="utf-8")
        result = write_file(
            path=str(target),
            content="hello\n",
            project_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert result["status"] == "no_change"
        assert result["lines_added"] == 0
        assert result["lines_removed"] == 0
        assert result["diff"] == ""

    def test_syntax_error_propagated(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.py"
        result = write_file(
            path=str(target),
            content="def f(:\n    pass\n",
            project_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert result["status"] == "ok"  # файл записан
        assert result["syntax_error"] != ""
        # В _llm_text — предупреждение.
        assert "Синтаксическая ошибка" in result["_llm_text"]

    def test_path_outside_project_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.py"
        result = write_file(
            path=str(outside),
            content="x = 1\n",
            project_root=str(tmp_path),
        )
        # Не dict — строка с ошибкой.
        assert isinstance(result, str)
        assert "Ошибка" in result