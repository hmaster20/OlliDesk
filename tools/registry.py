"""Реестр инструментов (декларативный список).

Централизованное место регистрации всех инструментов. UI может импортировать
список ALL_TOOLS для регистрации в одном месте.
"""
from __future__ import annotations

from tools import (
    attached_files,
    codebase_search,
    current_datetime,
    exchange_rate,
    file_ops,
    git_ops_tool,
    weather,
    web_search,
)

# Список всех инструментов для явной регистрации
ALL_TOOLS = [
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
    git_ops_tool.get_git_status,
    git_ops_tool.create_snapshot,
    git_ops_tool.undo_last_snapshot,
    # attached_files
    attached_files.read_attached_file,
]
