"""Состояние и фазы исследования проекта агентом."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Эвристики: какие файлы считаются «точкой входа» и «конфигом сборки»
# ---------------------------------------------------------------------------

_ENTRY_HINTS: tuple[str, ...] = (
    "main.py", "app.py", "cli.py", "__main__.py",
    "program.cs", "startup.cs",
    "index.js", "index.ts", "index.jsx", "index.tsx",
    "server.py", "wsgi.py", "asgi.py", "manage.py",
)

_CONFIG_HINTS: tuple[str, ...] = (
    "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
    "package.json", "tsconfig.json", "cargo.toml", "go.mod",
    "pom.xml", "build.gradle", "makefile", "dockerfile",
    "docker-compose.yml", "*.csproj", "*.sln",
    "readme.md", "readme.rst", "agents.md",
)


def _is_hint(path: str, hints: tuple[str, ...]) -> bool:
    """True, если basename пути совпадает с подсказкой."""
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

    RECON = "recon"
    DEEP_DIVE = "deep_dive"
    SELF_CHECK = "self_check"
    SYNTHESIS = "synthesis"


# ---------------------------------------------------------------------------
# Состояние
# ---------------------------------------------------------------------------


@dataclass
class InvestigationState:
    """Что агент уже изучил в текущем прогоне."""

    listed_dirs: set[str] = field(default_factory=set)
    read_files: set[str] = field(default_factory=set)
    read_with_content: set[str] = field(default_factory=set)
    searched_queries: list[str] = field(default_factory=list)
    total_chars_read: int = 0
    nudges_fired: set[str] = field(default_factory=set)
    verify_nudges_fired: int = 0

    def note_call(
        self, name: str, arguments: dict, result_content: str, is_error: bool
    ) -> None:
        """Запоминает успешный вызов инструмента."""
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
        """Есть ли минимум доказательной базы для ответа об архитектуре.

        Два уровня:
          1. Полный: 4+ файла, точка входа и конфиг/README.
          2. Смягчённый: 6+ файлов и точка входа — конфиг может отсутствовать
             (в проекте нет README, или модель не нашла — это не повод
             застревать в DEEP_DIVE навсегда).
        """
        if (
            len(self.read_with_content) >= 4
            and self.read_entry_point
            and self.read_config
        ):
            return True
        if len(self.read_with_content) >= 6 and self.read_entry_point:
            return True
        return False

    def summary(self) -> str:
        """Короткая сводка для инъекции в промпт."""
        return (
            f"каталогов просмотрено: {len(self.listed_dirs)}, "
            f"файлов прочитано: {len(self.read_with_content)} "
            f"(~{self.total_chars_read // 1000}к символов), "
            f"поисков по коду: {len(self.searched_queries)}"
        )

    def read_files_preview(self, limit: int = 10) -> str:
        """Список прочитанных файлов (для промпта)."""
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
    """True, если запрос требует глубокого исследования проекта."""
    if not text:
        return False
    return bool(_RESEARCH_TRIGGERS.search(text))


def phase_for(
    state: InvestigationState,
    open_hypotheses: int = 0,
    *,
    iteration: int = 0,
    max_iterations: int = 0,
) -> Phase:
    """Определяет текущую фазу по состоянию и числу открытых гипотез.

    Args:
        state: Накопленное состояние исследования.
        open_hypotheses: Сколько гипотез в статусе ``open``.
        iteration: Текущая итерация цикла (0-based).
        max_iterations: Общий лимит итераций.

    Returns:
        Фаза исследования.
    """
    # Аварийное условие: подходим к лимиту итераций — принудительно
    # переводим в SYNTHESIS, чтобы модель успела дать ответ. Иначе цикл
    # прервётся по «повтору вызовов» или лимиту БЕЗ финального текста.
    if max_iterations > 0 and state.has_deep_tools:
        remaining = max_iterations - iteration
        if remaining <= 2:
            return Phase.SYNTHESIS

    if not state.has_deep_tools:
        return Phase.RECON
    if not state.sufficient_for_synthesis:
        return Phase.DEEP_DIVE
    if open_hypotheses > 0:
        return Phase.SELF_CHECK
    if "selfcheck" not in state.nudges_fired:
        return Phase.SELF_CHECK
    return Phase.SYNTHESIS


# ---------------------------------------------------------------------------
# Нуджи
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

_NUDGE_HYPOTHESIS_FIRST = (
    "Ты уже прочитал несколько файлов, но НЕ сформулировал ни одной гипотезы. "
    "Прежде чем читать дальше — вызови record_hypothesis с конкретным "
    "утверждением о проекте, которое можно проверить чтением кода. Например: "
    "«Точка входа — main.py» или «UI построен на PySide6». Потом проверь "
    "гипотезу чтением и вызови update_hypothesis."
)

_NUDGE_DEPS = (
    "Ты прочитал несколько файлов, но не проследил зависимости. "
    "Возьми точку входа, найди в ней импорты/using/require и прочитай "
    "1–2 ключевых модуля, на которые она ссылается. Потом — их зависимости. "
    "Архитектура — это граф, а не набор файлов."
)

_NUDGE_VERIFY = (
    "У тебя остались НЕПРОВЕРЕННЫЕ гипотезы (status='open' в списке выше). "
    "Проверь их: прочитай соответствующий файл или сделай search_codebase, "
    "и вызови update_hypothesis со статусом verified/refuted/partial и "
    "конкретным доказательством."
)

_NUDGE_SELFCHECK = (
    "У тебя уже достаточно прочитанного материала и все гипотезы закрыты. "
    "Прежде чем ответить — проверь себя честно:\n"
    "  1) Я знаю, с какого файла проект запускается?\n"
    "  2) Я знаю ключевые внешние зависимости (фреймворк, БД, API)?\n"
    "  3) Я могу описать поток данных: вход → обработка → выход?\n"
    "  4) Я могу назвать 5+ компонентов и их связи?\n"
    "Если на любой из этих вопросов ответ «нет» — у тебя ещё есть итерации, "
    "продолжай исследование. Если «да» — дай финальный структурированный "
    "ответ с ASCII-схемой."
)


# Человекочитаемые статусы для UI
NUDGE_STATUS_LABELS: dict[str, str] = {
    "recon": "🔁 Прошу прочитать ключевые файлы…",
    "entry": "🔁 Прошу найти точку входа…",
    "config": "🔁 Прошу открыть конфиг сборки…",
    "hypothesis_first": "💡 Прошу сформулировать гипотезу…",
    "deps": "🔁 Прошу проследить зависимости…",
    "verify_hypotheses": "🔬 Прошу проверить гипотезы…",
    "selfcheck": "🪞 Прошу самопроверку перед ответом…",
}


def pick_nudge(
    state: InvestigationState,
    research_mode: bool,
    *,
    open_hypotheses: int = 0,
    hypotheses_available: bool = False,
) -> tuple[str, str] | None:
    """Выбирает следующий нудж по состоянию."""
    if not research_mode:
        return None

    fired = state.nudges_fired

    if "recon" not in fired and not state.has_deep_tools:
        return "recon", _NUDGE_RECON

    if "entry" not in fired and state.has_deep_tools and not state.read_entry_point:
        return "entry", _NUDGE_ENTRY

    if "config" not in fired and state.has_deep_tools and not state.read_config:
        return "config", _NUDGE_CONFIG

    has_recorded = open_hypotheses > 0 or "hypothesis_first" in fired

    if (
        hypotheses_available
        and "hypothesis_first" not in fired
        and not has_recorded
        and len(state.read_with_content) >= 2
    ):
        return "hypothesis_first", _NUDGE_HYPOTHESIS_FIRST

    if "deps" not in fired and 2 <= len(state.read_with_content) < 5:
        return "deps", _NUDGE_DEPS

    if (
        hypotheses_available
        and open_hypotheses > 0
        and state.verify_nudges_fired < 2
    ):
        state.verify_nudges_fired += 1
        return "verify_hypotheses", _NUDGE_VERIFY

    if (
        "selfcheck" not in fired
        and state.sufficient_for_synthesis
        and open_hypotheses == 0
    ):
        return "selfcheck", _NUDGE_SELFCHECK

    return None