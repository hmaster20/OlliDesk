"""Состояние и фазы исследования проекта агентом.

Задача — заставить 7B-модель не останавливаться на `list_directory`, а
последовательно копать проект: обзор → точка входа/конфиг → зависимости →
синтез. Управление поведением идёт двумя рычагами:

1. **Фаза** (RECON → DEEP_DIVE → SELF_CHECK → SYNTHESIS) вычисляется по
   накопленному состоянию и подмешивается в системный промпт. Модель видит
   «где она сейчас» и что от неё нужно на этом шаге.

2. **Градуированные нуджи** (`pick_nudge`) — если модель пытается дать
   финальный ответ раньше времени, цикл подсовывает ровно один следующий шаг.
   Каждый нудж срабатывает не более одного раза за прогон.

Состояние живёт отдельно от `messages`: история сжимается
(`compact_history`), а факты о том, что прочитано, — нет.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Эвристики: какие файлы считаются «точкой входа» и «конфигом сборки»
# ---------------------------------------------------------------------------

# Точки входа — файл, с которого стартует приложение.
_ENTRY_HINTS: tuple[str, ...] = (
    "main.py",
    "app.py",
    "cli.py",
    "__main__.py",
    "program.cs",
    "startup.cs",
    "index.js",
    "index.ts",
    "index.jsx",
    "index.tsx",
    "server.py",
    "wsgi.py",
    "asgi.py",
    "manage.py",
)

# Конфиги сборки/зависимостей — откуда виден стек.
_CONFIG_HINTS: tuple[str, ...] = (
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "package.json",
    "tsconfig.json",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "makefile",
    "dockerfile",
    "docker-compose.yml",
    "*.csproj",
    "*.sln",
    "readme.md",
    "readme.rst",
    "agents.md",
)


def _is_hint(path: str, hints: tuple[str, ...]) -> bool:
    """True, если basename пути совпадает с подсказкой (или её wildcard-формой)."""
    low = path.lower()
    base = low.rsplit("/", 1)[-1]
    for h in hints:
        if h.startswith("*."):
            if base.endswith(h[1:]):
                return True
        elif base == h or low.endswith("/" + h):
            return True
    return False


# ---------------------------------------------------------------------------
# Фазы исследования
# ---------------------------------------------------------------------------


class Phase(str, Enum):
    """Фаза исследовательского прогона."""

    RECON = "recon"            # обзор: каталоги, README, конфиг
    DEEP_DIVE = "deep_dive"    # чтение ключевых файлов, трассировка зависимостей
    SELF_CHECK = "self_check"  # проверка доказательной базы перед ответом
    SYNTHESIS = "synthesis"    # сборка финального ответа


# ---------------------------------------------------------------------------
# Состояние
# ---------------------------------------------------------------------------


@dataclass
class InvestigationState:
    """Что агент уже изучил в текущем прогоне.

    Отдельно от `messages`: история может сжиматься (`compact_history`),
    а эти факты — нет. Именно они определяют, «дозрел» ли агент до ответа.
    """

    listed_dirs: set[str] = field(default_factory=set)
    read_files: set[str] = field(default_factory=set)
    read_with_content: set[str] = field(default_factory=set)
    searched_queries: list[str] = field(default_factory=list)
    total_chars_read: int = 0
    nudges_fired: set[str] = field(default_factory=set)

    # --- наблюдатели ---

    def note_call(self, name: str, arguments: dict, result_content: str, is_error: bool) -> None:
        """Запоминает успешный вызов инструмента.

        Ошибки игнорируются: неудачное чтение — не доказательство изучения.
        """
        if is_error:
            return
        if name == "read_file":
            path = str(arguments.get("path") or "").strip()
            if path:
                norm = path.replace("\\", "/")
                self.read_files.add(norm)
                if result_content and len(result_content) > 50:
                    self.read_with_content.add(norm)
                    self.total_chars_read += len(result_content)
        elif name == "list_directory":
            path = str(arguments.get("path") or ".").strip()
            self.listed_dirs.add(path.replace("\\", "/"))
        elif name == "search_codebase":
            query = str(arguments.get("query") or "").strip()
            if query:
                self.searched_queries.append(query)

    # --- оценки «глубины» ---

    @property
    def has_deep_tools(self) -> bool:
        """Прочитан хотя бы один файл или сделан поиск по коду."""
        return bool(self.read_with_content) or bool(self.searched_queries)

    @property
    def read_entry_point(self) -> bool:
        """Открыт ли файл-кандидат в точку входа."""
        return any(_is_hint(p, _ENTRY_HINTS) for p in self.read_with_content)

    @property
    def read_config(self) -> bool:
        """Открыт ли файл-конфиг или README."""
        return any(_is_hint(p, _CONFIG_HINTS) for p in self.read_with_content)

    @property
    def sufficient_for_synthesis(self) -> bool:
        """Есть ли минимум доказательной базы для ответа об архитектуре."""
        return (
            len(self.read_with_content) >= 4
            and self.read_entry_point
            and self.read_config
        )

    def summary(self) -> str:
        """Короткая сводка для инъекции в промпт — модель видит свой путь."""
        return (
            f"каталогов просмотрено: {len(self.listed_dirs)}, "
            f"файлов прочитано: {len(self.read_with_content)} "
            f"(~{self.total_chars_read // 1000}к символов), "
            f"поисков по коду: {len(self.searched_queries)}"
        )

    def read_files_preview(self, limit: int = 10) -> str:
        """Список прочитанных файлов (для промпта) с усечением."""
        if not self.read_with_content:
            return "(пока ни одного)"
        items = sorted(self.read_with_content)
        head = items[:limit]
        suffix = f", … и ещё {len(items) - limit}" if len(items) > limit else ""
        return ", ".join(head) + suffix


# ---------------------------------------------------------------------------
# Детектор исследовательского вопроса
# ---------------------------------------------------------------------------

_RESEARCH_TRIGGERS = re.compile(
    r"(?:"
    r"архитектур|"
    r"структур[аыуе]?\s+проект|"
    r"схем[аыуе]|"
    r"компонент|"
    r"зависимост|"
    r"взаимосвяз|"
    r"как\s+(?:устроен|работает|связан|организован)|"
    r"из\s+чего\s+состоит|"
    r"разбер[иы]|разобраться|"
    r"изучи(?:ть)?\s+(?:проект|код|кодовую\s+базу|репозиторий)|"
    r"проанализируй\s+(?:проект|код|кодовую\s+базу)|"
    r"опиши\s+(?:архитектуру|структуру|компоненты)|"
    r"data\s*flow|call\s*chain|dependen(?:cy|cies)|"
    r"что\s+за\s+проект|о\s+чём\s+проект|"
    r"нарисуй\s+(?:схему|диаграмму)"
    r")",
    re.IGNORECASE,
)


def is_research_question(text: str) -> bool:
    """True, если запрос требует глубокого исследования проекта.

    Отсекает бытовые вопросы («погода», «дата», «что такое SOLID») —
    для них research-режим не включается, поведение прежнее.
    """
    if not text:
        return False
    return bool(_RESEARCH_TRIGGERS.search(text))


def phase_for(state: InvestigationState) -> Phase:
    """Определяет текущую фазу по накопленному состоянию.

    Фаза — вычисляемая величина, не хранимое поле: если агент «провалился
    назад» (например, прочитал ещё файлов после достаточного объёма),
    состояние это заметит.
    """
    if not state.has_deep_tools:
        return Phase.RECON
    if not state.sufficient_for_synthesis:
        return Phase.DEEP_DIVE
    if "selfcheck" not in state.nudges_fired:
        return Phase.SELF_CHECK
    return Phase.SYNTHESIS


# ---------------------------------------------------------------------------
# Нуджи — «следующий шаг», который подсовывается модели
# ---------------------------------------------------------------------------

_NUDGE_RECON = (
    "Ты посмотрел список каталогов, но НЕ прочитал ни одного файла. "
    "Этого недостаточно даже для обзора. Начни с README.md и конфига сборки "
    "(pyproject.toml / package.json / *.csproj) — вызови read_file."
)

_NUDGE_ENTRY = (
    "Ты прочитал файлы, но НЕ открыл ни одного кандидата на точку входа "
    "(main.py, Program.cs, index.js, app.py, __main__.py). Без точки входа "
    "ты не сможешь описать, как проект запускается и какие модули в нём "
    "главные. Найди и прочитай её."
)

_NUDGE_CONFIG = (
    "Ты прочитал исходники, но НЕ открыл конфиг сборки или README. "
    "Именно там виден стек (фреймворк, БД, внешние зависимости), и без них "
    "ты не отличишь C# от Python и Qt от Electron. Прочитай "
    "pyproject.toml / package.json / *.csproj / README.md."
)

_NUDGE_DEPS = (
    "Ты прочитал несколько файлов, но не проследил зависимости. "
    "Возьми точку входа, найди в ней импорты/using/require и прочитай "
    "1–2 ключевых модуля, на которые она ссылается. Потом — их зависимости. "
    "Архитектура — это граф, а не набор файлов."
)

_NUDGE_SELFCHECK = (
    "У тебя уже достаточно прочитанного материала. Прежде чем ответить — "
    "проверь себя честно:\n"
    "  1) Я знаю, с какого файла проект запускается?\n"
    "  2) Я знаю ключевые внешние зависимости (фреймворк, БД, API)?\n"
    "  3) Я могу описать поток данных: вход → обработка → выход?\n"
    "  4) Я могу назвать 5+ компонентов и их связи?\n"
    "Если на любой из этих вопросов ответ «нет» — у тебя ещё есть итерации, "
    "продолжай исследование (read_file / search_codebase). Если «да» — дай "
    "финальный структурированный ответ с ASCII-схемой."
)


# Человекочитаемые статусы для UI (используются в on_status)
NUDGE_STATUS_LABELS: dict[str, str] = {
    "recon": "🔁 Прошу прочитать ключевые файлы…",
    "entry": "🔁 Прошу найти точку входа…",
    "config": "🔁 Прошу открыть конфиг сборки…",
    "deps": "🔁 Прошу проследить зависимости…",
    "selfcheck": "🪞 Прошу самопроверку перед ответом…",
}


def pick_nudge(state: InvestigationState, research_mode: bool) -> tuple[str, str] | None:
    """Выбирает следующий нудж по состоянию.

    Возвращает ``(ключ, текст)`` или ``None``, если нуджей больше нет —
    тогда цикл принимает текущий текст как финальный ответ.

    Порядок проверок задаёт приоритет: сначала «совсем ничего не прочитал»,
    затем «нет точки входа», затем «нет конфига», затем «мало зависимостей»,
    затем «проверь себя». Каждый нудж срабатывает один раз за прогон.
    """
    if not research_mode:
        return None

    fired = state.nudges_fired

    if "recon" not in fired and not state.has_deep_tools:
        return "recon", _NUDGE_RECON

    if "entry" not in fired and state.has_deep_tools and not state.read_entry_point:
        return "entry", _NUDGE_ENTRY

    if "config" not in fired and state.has_deep_tools and not state.read_config:
        return "config", _NUDGE_CONFIG

    if "deps" not in fired and 2 <= len(state.read_with_content) < 5:
        return "deps", _NUDGE_DEPS

    if "selfcheck" not in fired and state.sufficient_for_synthesis:
        return "selfcheck", _NUDGE_SELFCHECK

    return None