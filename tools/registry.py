"""Реестр инструментов (декларативный список).

Централизованное место регистрации всех инструментов. ``ChatPanel._build_registry``
обходит ``ALL_TOOLS`` и регистрирует каждый в ``ToolRegistry``; UI не держит
собственный список импортов.

Порядок важен для читаемости (группировка по файлам), но не для поведения:
``ToolRegistry`` разрешает инструменты по ``mode`` и ``policy``, а не по порядку.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from tools import test_ops, codebase_search

from tools import (
    attached_files,
    browser_ops,
    codebase_search,
    current_datetime,
    exchange_rate,
    file_ops,
    git_ops_tool,
    hypothesis,
    shell_ops,
    test_ops,
    weather,
    web_search,
)

# Список всех инструментов для явной регистрации.
# Аннотация `list[Callable[..., Any]]` нужна, чтобы mypy не выводил для
# декорированных функций тип `function` (он несовместим с сигнатурой
# `ToolRegistry.register(func: Callable[..., Any])`).
ALL_TOOLS: list[Callable[..., Any]] = [
    # file_ops
    file_ops.read_file,
    file_ops.list_directory,
    file_ops.write_file,
    # codebase_search
    codebase_search.search_codebase,
    # web_search
    web_search.web_search,
    # weather
    weather.get_weather,
    # current_datetime
    current_datetime.get_current_datetime,
    # exchange_rate
    exchange_rate.get_exchange_rate,
    exchange_rate.get_exchange_rates_multi,
    # git_ops_tool
    git_ops_tool.tool_git_status,
    git_ops_tool.tool_create_snapshot,
    # git_ops_tool.tool_undo_snapshot,
    git_ops_tool.tool_undo_file_snapshot,
    git_ops_tool.tool_git_commit,
    # attached_files (register unconditionally; ChatPanel filters by availability)
    attached_files.read_attached_file,
    # hypothesis
    hypothesis.record_hypothesis,
    hypothesis.update_hypothesis,
    hypothesis.list_hypotheses,
    # shell_ops
    shell_ops.run_shell_command,
    test_ops.run_tests,
    codebase_search.search_codebase,
    codebase_search.find_references,
    shell_ops.run_shell_command,
    # [2.11] browser_*
    browser_ops.browser_open,
    browser_ops.browser_snapshot,
    browser_ops.browser_click,
    browser_ops.browser_fill,
    browser_ops.browser_close,
]
