"""Инструмент выполнения shell-команд (PowerShell) с жёсткими рамками.

Решение из PLAN_MODERNIZATION.md §4.8.C: агент без shell не может
проверить `Test-Path`, посчитать строки или запустить тесты. Цена —
выполнение произвольных команд от имени пользователя, поэтому:

* ``policy=ASK_FIRST`` — пользователь всегда подтверждает вызов;
* ``modes=[AgentMode.AGENT]`` — в CHAT/PLAN инструмент недоступен;
* чёрный список разрушительных команд — защита от ошибок 7B-модели,
  НЕ от злоумышленника (обходится через ``cmd /c``, base64,
  ``powershell -EncodedCommand``);
* песочница cwd — через общий ``SandboxedPathResolver`` (тот же,
  что у ``tools/file_ops``): запрет выхода за пределы проекта и
  записи в ``.git``/``.ollidesk``;
* лимит вывода: первые ``MAX_OUTPUT_CHARS`` символов, чтобы Observation
  не съел окно контекста.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy
from core.sandbox import SandboxedPathResolver  # NEW

# Лимит вывода одной команды. Промежуточные результаты (grep, ls) редко
# превышают 5000 символов; более длинный вывод всё равно не влезет в
# Observation-бюджет, а модель увидит только хвост вперемешку с мусором.
MAX_OUTPUT_CHARS = 5000

# Максимальный таймаут: 5 минут. Выше — почти всегда ошибка модели
# (одна команда повесит UI), а не реальная задача.
MAX_TIMEOUT_S = 300

# Разрушительные команды. Список — защита от ошибок 7B-модели, а не от
# злоумышленника: ``powershell -EncodedCommand <base64>`` его обходит.
# Поэтому policy=ASK_FIRST + песочница cwd обязательны.
_BLACKLIST: tuple[re.Pattern[str], ...] = (
    # POSIX-стиль: rm -rf / rm -fr / rm -r
    re.compile(r"\brm\s+-[a-zA-Z]*[rf]", re.I),
    # PowerShell: Remove-Item -Recurse -Force в любом порядке
    re.compile(r"\bRemove-Item\b[^\n]*?-Recurse\b[^\n]*?-Force\b", re.I),
    re.compile(r"\bRemove-Item\b[^\n]*?-Force\b[^\n]*?-Recurse\b", re.I),
    # cmd: del /f /s /q, del /q /s /f
    re.compile(r"\b(?:del|erase)\s+/[fsq]+", re.I),
    # cmd: rd /s, rmdir /s
    re.compile(r"\b(?:rd|rmdir)\s+/s\b", re.I),
    # format C:
    re.compile(r"\bformat\s+[a-z]:", re.I),
    # shutdown: только с флагом (/r, /s, -h, …) или в начале команды.
    # Не срабатывает на `Get-Content shutdown.log` — там за `shutdown` идёт `.`.
    re.compile(r"(?:^|[;&|]\s*)shutdown\s+[-/]\w", re.I),
    # реестр
    re.compile(r"\breg\s+add\b", re.I),
    re.compile(r"\bSet-ItemProperty\b", re.I),
    # curl | iex, iwr | iex, Invoke-WebRequest ... | iex
    re.compile(r"\bcurl\b[^\n|]*\|\s*(?:iex|Invoke-Expression)", re.I),
    re.compile(r"\biwr\b[^\n|]*\|\s*(?:iex|Invoke-Expression)", re.I),
    re.compile(r"\bInvoke-WebRequest\b[^\n|]*\|\s*iex", re.I),
)


def _powershell_exe() -> str | None:
    """Путь к PowerShell: pwsh (Core) приоритетно, иначе powershell.exe."""
    for name in ("pwsh", "powershell.exe", "powershell"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _check_blacklist(command: str) -> str | None:
    """Возвращает описание сработавшего паттерна или None."""
    for pattern in _BLACKLIST:
        if pattern.search(command):
            return pattern.pattern
    return None


async def _run_powershell(
    ps_exe: str,
    command: str,
    cwd: Path,
    timeout: int,
) -> tuple[str, int]:
    """Запускает команду и возвращает (stdout+stderr, returncode).

    ``stderr=STDOUT`` — объединяем потоки, чтобы ошибка не «потерялась»
    в отдельном pipe (иначе пользователь видит «успех» с пустым выводом).
    """
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = await asyncio.create_subprocess_exec(
        ps_exe,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=creationflags,
    )
    try:
        out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return (
            f"⚠️ Команда превысила таймаут {timeout} с и была прервана.",
            -1,
        )
    return (
        out_bytes.decode("utf-8", errors="replace"),
        proc.returncode if proc.returncode is not None else 0,
    )


@tool(
    name="run_shell_command",
    description=(
        "Выполняет PowerShell-команду в корне проекта и возвращает вывод. "
        "Используй для проверки существования путей (Test-Path), подсчёта "
        "строк (Get-Content ... | Measure-Object -Line), запуска тестов и "
        "т.п. Работает ТОЛЬКО в режиме Agent и только после подтверждения "
        "пользователя. cwd — относительно корня проекта; выход за пределы "
        "проекта и в .git/.ollidesk запрещён. Вывод обрезается до ~5000 "
        "символов."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
    is_mutating=True,
    category="shell",
)
async def run_shell_command(
    command: str,
    cwd: str = "",
    timeout: int = 30,
    project_root: str = "",
) -> str:
    """Выполняет PowerShell-команду в песочнице проекта.

    Args:
        command: Команда PowerShell (например, ``Test-Path .\\README.md``).
        cwd: Рабочий каталог относительно ``project_root``. По умолчанию —
            корень проекта.
        timeout: Таймаут в секундах (1..300, по умолчанию 30).
        project_root: Корень проекта (инжектируется через AgentContext).

    Returns:
        Строка с заголовком ``$ command``, выводом и кодом выхода.
        При ошибке — строка, начинающаяся с ``Ошибка:``.
    """
    if not command or not command.strip():
        return "Ошибка: пустая команда."

    # 1. Песочница: без project_root выполнение бессмысленно и опасно
    #    (команда попадёт в произвольный каталог — например, домашний).
    #    NEW: используется общий SandboxedPathResolver вместо локальной
    #    копии `_resolve_cwd`. Отличие от file_ops — «пустой cwd == корень
    #    проекта» и «пустой project_root == отказ»; обе семантики относятся
    #    к shell-инструменту, а не к резолверу, поэтому инлайн здесь.
    if not project_root:
        return "Ошибка: run_shell_command требует открытый проект (project_root не передан)."
    resolver = SandboxedPathResolver(project_root)
    work_dir = resolver.root if not cwd else resolver.resolve(cwd)
    if work_dir is None:
        return (
            "Ошибка: cwd вне проекта или внутри служебной директории "
            "(.git/.ollidesk). Укажи путь относительно корня проекта."
        )
    if not work_dir.is_dir():
        return f"Ошибка: каталог не найден: {work_dir}"

    # 2. PowerShell: pwsh предпочтительнее (Core), powershell.exe — запасной.
    ps_exe = _powershell_exe()
    if ps_exe is None:
        return (
            "Ошибка: PowerShell не найден в PATH. Установи PowerShell Core "
            "(pwsh) или включи Windows PowerShell (powershell.exe)."
        )

    # 3. Чёрный список разрушительных команд (защита от ошибок модели).
    hit = _check_blacklist(command)
    if hit is not None:
        logger.warning(f"run_shell_command: blacklist hit ({hit}): {command[:120]}")
        return (
            "Ошибка: команда отклонена чёрным списком разрушительных "
            "операций. Переформулируй задачу без удаления/recurse-force/"
            "записи в реестр/shutdown/pipe-to-iex."
        )

    # 4. Нормализация таймаута.
    try:
        t = int(timeout)
    except (TypeError, ValueError):
        t = 30
    t = max(1, min(t, MAX_TIMEOUT_S))

    logger.info(f"run_shell_command: cwd={work_dir}, timeout={t}s, cmd={command[:200]}")

    # 5. Запуск.
    try:
        out, rc = await _run_powershell(ps_exe, command, work_dir, t)
    except FileNotFoundError as e:
        return f"Ошибка: не удалось запустить {ps_exe}: {e}"
    except Exception as e:
        logger.exception("run_shell_command failed")
        return f"Ошибка запуска команды: {e}"

    # 6. Усечение вывода.
    truncated = False
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS]
        truncated = True

    # 7. Формирование ответа.
    header = f"$ {command}\n(exit {rc}, cwd {work_dir.name})"
    body = out.rstrip() if out else "(пустой вывод)"
    footer = "\n[Вывод обрезан до 5000 символов]" if truncated else ""
    return f"{header}\n{body}{footer}"

def _resolve_cwd(cwd: str, project_root: str) -> Path | None:
    """Устаревшая обёртка для обратной совместимости тестов.

    Семантика совпадает с `run_shell_command`: пустой `cwd` — корень
    проекта, иначе резолв относительно корня; выход за проект → None.
    """
    resolver = SandboxedPathResolver(project_root)
    return resolver.root if not cwd else resolver.resolve(cwd)