# ui/chat/tool_labels.py
"""Словари меток для инструментов и режимов агента.

Вынесены из `ui/chat/panel.py`, потому что одни и те же словари
дублировались в `_on_tool_call_detected` и `_on_tool_started`, а также
чтобы вся «текстовка UI» лежала в одном месте.
"""

from __future__ import annotations

# --- Режимы --------------------------------------------------------------

# Явное описание режимов для тултипов селектора: что включено и почему.
MODE_DESCRIPTIONS: dict[int, str] = {
    0: (
        "💬 Chat — обычный диалог с моделью.\n"
        "Инструменты только для чтения: чтение файлов, поиск по коду, "
        "погода, время, курсы.\n"
        "Ничего не меняет на диске. Подходит для вопросов и объяснений."
    ),
    1: (
        "📋 Plan — исследование и планирование.\n"
        "Инструменты только для чтения: обзор каталогов, чтение файлов, "
        "поиск по коду.\n"
        "Запись и Git недоступны — режим для составления плана без изменений."
    ),
    2: (
        "🤖 Agent — полный доступ.\n"
        "Все инструменты: чтение, запись файлов, Git-снапшоты, поиск, web.\n"
        "Может менять проект (операции записи подтверждаются)."
    ),
}

# Имена режимов для заголовка сообщения ассистента.
MODE_NAMES: dict[int, str] = {0: "Чат", 1: "План", 2: "Агент"}

# Строковые идентификаторы режимов (для сохранения в конфиг).
MODE_IDS: dict[int, str] = {0: "chat", 1: "plan", 2: "agent"}
MODE_IDS_REVERSE: dict[str, int] = {"chat": 0, "plan": 1, "agent": 2}


# --- Инструменты ---------------------------------------------------------

# Короткие человекочитаемые имена для диалога подтверждения.
TOOL_DISPLAY_NAMES: dict[str, str] = {
    "read_file": "Чтение файла",
    "write_file": "Запись файла",
    "list_directory": "Просмотр директории",
    "search_codebase": "Поиск в кодовой базе",
    "web_search": "Поиск в интернете",
    "get_weather": "Погода",
    "get_current_datetime": "Текущие дата и время",
    "get_exchange_rate": "Курс валюты",
    "get_exchange_rates_multi": "Курсы нескольких валют",
    "get_git_status": "Статус Git",
    "create_snapshot": "Создание снапшота Git",
    "undo_last_snapshot": "Откат снапшота Git",
    "read_attached_file": "Чтение прикреплённого файла",
    "record_hypothesis": "Запись гипотезы",
    "update_hypothesis": "Обновление гипотезы",
    "list_hypotheses": "Список гипотез",
}

# Метки с эмодзи для строк журнала (используются в `_on_tool_started`).
TOOL_ACTIVITY_LABELS: dict[str, str] = {
    "read_file": "📖 Чтение файла",
    "write_file": "✏️ Запись файла",
    "list_directory": "📁 Просмотр директории",
    "search_codebase": "🔍 Поиск в кодовой базе",
    "web_search": "🌐 Поиск в интернете",
    "get_weather": "🌤 Погода",
    "get_current_datetime": "🕒 Дата/время",
    "get_exchange_rate": "💱 Курс валюты",
    "get_exchange_rates_multi": "💱 Курсы валют",
    "get_git_status": "🔎 Проверка Git-статуса",
    "create_snapshot": "📸 Создание снапшота Git",
    "undo_last_snapshot": "⏪ Откат снапшота Git",
    "read_attached_file": "📄 Дочитывание файла",
    "record_hypothesis": "💡 Запись гипотезы",
    "update_hypothesis": "🔬 Обновление гипотезы",
    "list_hypotheses": "📋 Список гипотез",
}


# --- Прочие константы, использовавшиеся в panel.py -----------------------

# Инструменты, которые в Chat-режиме должны выполняться без подтверждения.
CHAT_AUTO_TOOLS: tuple[str, ...] = (
    "web_search",
    "read_file",
    "search_codebase",
    "list_directory",
    "get_weather",
    "get_current_datetime",
    "get_exchange_rate",
    "get_exchange_rates_multi",
    "get_git_status",
    "read_attached_file",
    "record_hypothesis",
    "update_hypothesis",
    "list_hypotheses",
)

# Команды-префиксы для прикрепления путей (в стиле @web).
PATH_COMMANDS: tuple[str, ...] = ("@dir", "@file", "@path")

# Сколько символов прикрепленного файла показывать в системном промпте,
# когда остаётся мало контекста.
MIN_ATTACHED_BUDGET: int = 600

# Сколько чанков запрашивать у векторного поиска перед дедупликацией по файлам.
RAG_N_RESULTS: int = 8