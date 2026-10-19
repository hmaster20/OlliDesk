"""Состояние и фазы исследования проекта агентом."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Эвристики: какие файлы считаются «точкой входа» и «конфигом сборки»
# ---------------------------------------------------------------------------

_ENTRY_HINTS: tuple[str, ...] = (
    # Python
    "main.py",
    "app.py",
    "cli.py",
    "__main__.py",
    "server.py",
    "wsgi.py",
    "asgi.py",
    "manage.py",
    # C# / .NET
    "program.cs",
    "startup.cs",
    # JS/TS
    "index.js",
    "index.ts",
    "index.jsx",
    "index.tsx",
    # [D7] Ruby / Go
    "main.rb",
    "config.ru",
    "main.go",
    # [D7] IaC
    "main.tf",
    "variables.tf",
    "outputs.tf",
    "site.yml",
    "site.yaml",
    "playbook.yml",
    "playbook.yaml",
    "vagrantfile",  # без расширения — .lower() совпадёт с Vagrantfile
)

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
# Пороги «достаточно изучено»
# ---------------------------------------------------------------------------

# Порог по объёму прочитанного: если модель уже прочитала 40 КБ кода и
# открыла точку входа — этого хватит для ответа об архитектуре. Отсекает
# бесконечное чтение одного большого файла.
_ENOUGH_CHARS = 40_000

# Порог по числу успешных вызовов read_file (в т.ч. разных offset одного
# файла). 10 успешных чтений с точкой входа = достаточно.
_ENOUGH_READS = 10

# Сколько раз можно прочитать ОДИН файл, прежде чем это считается
# застреванием. На нормальный файл < 50 КБ уходит 1–3 чтения.
_MAX_READS_PER_FILE = 5


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

    # NEW: сколько успешных read_file было ВООБЩЕ (включая повторные чтения
    # одного файла с разными offset) и сколько раз читался каждый файл.
    successful_reads: int = 0
    reads_per_file: dict[str, int] = field(default_factory=dict)

    # Entry-point кандидаты из ProjectIntel. Если модель не открыла
    # entry-point через read_file, но индекс знает кандидата и прочитано
    # ≥2 файла — этого достаточно для перехода к синтезу (deterministic
    # fallback вместо бесконечных нуджей).
    known_entry_points: list[str] = field(default_factory=list)

    # Счётчики ВСЕХ вызовов и только успешных — для валидации Observation
    # (React.md 2.4). Если модель «пыталась», но все вызовы упали
    # (successful_observations == 0), AgentLoop не даст сформировать Final
    # Answer: сначала нужно честно перепробовать другой инструмент/путь.
    attempted_calls: int = 0
    successful_observations: int = 0

    def note_call(self, name: str, arguments: dict, result_content: str, is_error: bool) -> None:
        """Запоминает вызов инструмента.

        ``attempted_calls`` инкрементится ВСЕГДА (даже при ошибке) —
        это индикатор «модель пыталась». ``successful_observations`` — только
        для успешных вызовов; используется в AgentLoop для валидации
        Observation (React.md 2.4): «все вызовы упали» — отдельный кейс,
        отличный от «модель ничего не вызывала».
        """
        self.attempted_calls += 1
        if is_error:
            return
        self.successful_observations += 1
        if name == "read_file":
            path = str(arguments.get("path") or "").strip()
            if path:
                norm = path.replace("\\", "/")
                self.read_files.add(norm)
                self.successful_reads += 1
                self.reads_per_file[norm] = self.reads_per_file.get(norm, 0) + 1
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
    def stuck_on_one_file(self) -> bool:
        """True, если модель читает ОДИН файл слишком много раз.

        Типичный случай: файл 150 КБ, модель читает по 1000 символов и
        делает 150 вызовов. Это застревание, а не исследование.
        """
        return any(c >= _MAX_READS_PER_FILE for c in self.reads_per_file.values())

    @property
    def max_file_reads(self) -> tuple[str, int]:
        """Файл с максимальным числом чтений и само число (для логов/нуджей)."""
        if not self.reads_per_file:
            return ("", 0)
        path = max(self.reads_per_file, key=lambda k: self.reads_per_file[k])
        return (path, self.reads_per_file[path])

    @property
    def sufficient_for_synthesis(self) -> bool:
        """Есть ли минимум доказательной базы для ответа об архитектуре."""
        if len(self.read_with_content) >= 4 and self.read_entry_point and self.read_config:
            return True
        if len(self.read_with_content) >= 6 and self.read_entry_point:
            return True
        if self.total_chars_read >= _ENOUGH_CHARS and self.read_entry_point:
            return True
        if self.successful_reads >= _ENOUGH_READS and self.read_entry_point:
            return True
        # [D4] Модель не открыла entry-point, но индекс знает кандидата —
        # считаем entry покрытым детерминированно.
        if self.known_entry_points and len(self.read_with_content) >= 2:
            return True
        return False

    def summary(self) -> str:
        """Короткая сводка для инъекции в промпт."""
        return (
            f"каталогов просмотрено: {len(self.listed_dirs)}, "
            f"файлов прочитано: {len(self.read_with_content)} "
            f"(всего {self.successful_reads} чтений, ~{self.total_chars_read // 1000}к символов), "
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

    Порядок проверок важен — приоритет у аварийных условий:
      1. Застревание на одном файле → SYNTHESIS.
      2. Мало итераций осталось → SYNTHESIS.
      3. Дальше — обычная логика фаз.
    Returns:
        Фаза исследования.
    """
    # (1) Застревание на одном файле — цикл не имеет смысла продолжать.
    if state.has_deep_tools and state.stuck_on_one_file:
        return Phase.SYNTHESIS

    # (2) Аварийный переход к синтезу в конце прогона.
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
    "Ты прочитал исходники, но НЕ открыл конфиг сборки, AGENTS.md или README. "
    "AGENTS.md (если он есть) — курируемая сводка от автора: он важнее README, "
    "читай его ПЕРВЫМ. Затем конфиг сборки (*.csproj / *.sln / package.json / "
    "pyproject.toml / go.mod). Без них ты не отличишь C# от Python и Qt от WinForms."
)

_NUDGE_HYPOTHESIS_FIRST = (
    "Ты уже прочитал несколько файлов, но НЕ сформулировал ни одной гипотезы. "
    "Прежде чем читать дальше — вызови record_hypothesis с конкретным "
    "утверждением о проекте, которое можно проверить чтением кода."
)

_NUDGE_DEPS = (
    "Ты прочитал несколько файлов, но не проследил зависимости. "
    "Возьми точку входа, найди в ней импорты/using/require и прочитай "
    "1–2 ключевых модуля, на которые она ссылается. Потом — их зависимости. "
    "Архитектура — это граф, а не набор файлов."
)

_NUDGE_VERIFY = (
    "У тебя остались НЕПРОВЕРЕННЫЕ гипотезы (status='open' в списке выше). "
    "Проверь их: прочитай соответствующий файл и вызови update_hypothesis "
    "со статусом verified/refuted/partial и конкретным доказательством."
)

_NUDGE_DIFFERENT_FILE = (
    "Ты читаешь ОДИН И ТОТ ЖЕ файл много раз подряд. Это неэффективно. "
    "Большой файл целиком читать не нужно — для понимания архитектуры хватит "
    "ключевых фрагментов: начало (класс, поля, свойства), методы по имени "
    "(через search_codebase), конец. Используй search_codebase для поиска "
    "конкретных сущностей или прочитай ДРУГИЕ файлы (точка входа уже изучена). "
    "Начинай формировать финальный ответ."
)

_NUDGE_SELFCHECK = (
    "У тебя уже достаточно прочитанного материала. Прежде чем ответить — "
    "проверь себя честно:\n"
    "  1) Я знаю, с какого файла проект запускается?\n"
    "  2) Я знаю ключевые внешние зависимости (фреймворк, БД, API)?\n"
    "  3) Я могу описать поток данных: вход → обработка → выход?\n"
    "  4) Я могу назвать 5+ компонентов и их связи?\n"
    "Дай финальный структурированный ответ с ASCII-схемой."
)


# Человекочитаемые статусы для UI
NUDGE_STATUS_LABELS: dict[str, str] = {
    "recon": "🔁 Прошу прочитать ключевые файлы…",
    "entry": "🔁 Прошу найти точку входа…",
    "config": "🔁 Прошу открыть конфиг сборки…",
    "hypothesis_first": "💡 Прошу сформулировать гипотезу…",
    "deps": "🔁 Прошу проследить зависимости…",
    "verify_hypotheses": "🔬 Прошу проверить гипотезы…",
    "different_file": "⏹ Прошу переключиться с одного файла…",
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

    # Высший приоритет: застревание на одном файле.
    if "different_file" not in fired and state.stuck_on_one_file:
        return "different_file", _NUDGE_DIFFERENT_FILE

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

    if hypotheses_available and open_hypotheses > 0 and state.verify_nudges_fired < 2:
        state.verify_nudges_fired += 1
        return "verify_hypotheses", _NUDGE_VERIFY

    if "selfcheck" not in fired and state.sufficient_for_synthesis and open_hypotheses == 0:
        return "selfcheck", _NUDGE_SELFCHECK

    return None
