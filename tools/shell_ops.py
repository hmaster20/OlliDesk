"""Инструмент выполнения shell-команд (PowerShell) с жёсткими рамками.

0.1.1: политика перевёрнута с blacklist на **allowlist**. По умолчанию
разрешён только фиксированный набор безопасных команд; произвольные
команды требуют ``SecurityPolicy.allow_arbitrary_shell=True`` (dangerous
mode, включается явно в ⚙ → Безопасность). Даже в dangerous mode
чёрный список разрушительных команд (rm -rf, format, shutdown, …)
продолжает блокировать — это защита от ошибок модели, а не от
злоумышленника (``powershell -EncodedCommand`` его обходит; для этого
dangerous mode явный).

Каждый вызов пишется в ``~/.ollidesk/audit.db`` (``core.audit.AuditLog``):
timestamp, tool, args_hash, policy, approved, result_hash, error,
dangerous. Хеши — не сами данные — чтобы не плодить PII.

Песочница cwd — через общий ``SandboxedPathResolver`` (тот же, что
у ``tools/file_ops``): запрет выхода за пределы проекта и записи в
``.git``/``.ollidesk``.
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
from core.audit import AuditLog, get_audit_log
from core.config import AgentMode, ToolPolicy
from core.sandbox import SandboxedPathResolver
from core.security import SecurityPolicy

MAX_OUTPUT_CHARS = 5000
MAX_TIMEOUT_S = 300

# Разрушительные команды. Список — защита от ошибок 7B-модели, а не от
# злоумышленника: ``powershell -EncodedCommand <base64>`` его обходит.
# Поэтому policy=ASK_FIRST + песочница cwd + audit log обязательны.
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
    re.compile(r"(?:^|[;&|]\s*)shutdown\s+[-/]\w", re.I),
    # реестр
    re.compile(r"\breg\s+add\b", re.I),
    re.compile(r"\bSet-ItemProperty\b", re.I),
    # curl | iex, iwr | iex, Invoke-WebRequest ... | iex
    re.compile(r"\bcurl\b[^\n|]*\|\s*(?:iex|Invoke-Expression)", re.I),
    re.compile(r"\biwr\b[^\n|]*\|\s*(?:iex|Invoke-Expression)", re.I),
    re.compile(r"\bInvoke-WebRequest\b[^\n|]*\|\s*iex", re.I),
)

# 0.1.1: allowlist безопасных команд. Один большой regex на первый
# «смысловой» токен: имя командлета, либо `python -m <tool>`,
# либо `git <safe-sub>`, либо `dotnet|go|npm test`.
_ALLOWLIST_RE = re.compile(
    r"^\s*(?:"
    # PowerShell read-only командлеты
    r"(?:Test-Path|Get-ChildItem|Get-Content|Select-String|Measure-Object"
    r"|Get-Location|Resolve-Path|Get-FileHash)\b"
    # python -m <safe>
    r"|(?:python|python3|py)\s+-m\s+(?:pytest|py_compile|unittest|mypy|ruff)\b"
    # git read-only
    r"|git\s+(?:status|diff|log|show|branch|rev-parse|describe)\b"
    # test runners
    r"|(?:dotnet\s+test|go\s+test|npm\s+(?:test|run\s+test))\b"
    r")",
    re.IGNORECASE,
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


def _in_allowlist(command: str) -> bool:
    """True, если первый «смысловой» токен команды в allowlist."""
    return bool(_ALLOWLIST_RE.match(command))


async def _run_powershell(
    ps_exe: str,
    command: str,
    cwd: Path,
    timeout: int,
) -> tuple[str, int]:
    """Запускает команду и возвращает (stdout+stderr, returncode)."""
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


def _audit(
    command: str,
    cwd: str,
    *,
    approved: bool,
    result: str,
    is_error: bool,
    dangerous: bool,
    audit_log: AuditLog | None = None,
) -> None:
    """Пишет один вызов в audit.db. Ошибки записи не пробрасывает."""
    (audit_log or get_audit_log()).log(
        "run_shell_command",
        {"command": command, "cwd": cwd},
        policy="ask_first",
        approved=approved,
        result_content=result,
        is_error=is_error,
        dangerous=dangerous,
    )


@tool(
    name="run_shell_command",
    description=(
        "Выполняет PowerShell-команду в корне проекта и возвращает вывод. "
        "Работает ТОЛЬКО в режиме Agent и только после подтверждения "
        "пользователя. По умолчанию разрешён только безопасный набор: "
        "Test-Path, Get-ChildItem, Get-Content, Select-String, "
        "Measure-Object, Get-Location, Resolve-Path, Get-FileHash, "
        "python -m pytest|py_compile|unittest|mypy|ruff, "
        "git status|diff|log|show|branch|rev-parse|describe, "
        "dotnet test, go test, npm test. "
        "Произвольные команды — только в dangerous mode (⚙ → Безопасность)."
    ),
    policy=ToolPolicy.ASK_FIRST,
    modes=[AgentMode.AGENT],
    is_mutating=True,
    category="shell",
    capabilities=["shell", "fs.write"],  # shell может писать файлы
    timeout_s=0.0,  # управляется параметром `timeout` внутри (max 300с)
)
async def run_shell_command(
    command: str,
    cwd: str = "",
    timeout: int = 30,
    project_root: str = "",
    security: SecurityPolicy | None = None,
) -> str:
    """Выполняет PowerShell-команду в песочнице проекта (0.1.1).

    Порядок проверок:
      1. Blacklist — ВСЕГДА (``rm -rf``, ``format``, ``shutdown /r``,
         ``reg add``, ``curl | iex``). Не зависит от allowlist/dangerous mode.
      2. Allowlist — по умолчанию разрешён только безопасный набор
         (``Test-Path``, ``git status``, ``python -m pytest``, …).
      3. Dangerous mode (``allow_arbitrary_shell=True``) — снимает
         allowlist-ограничение, но blacklist остаётся.

    Args:
        command: Команда PowerShell.
        cwd: Рабочий каталог относительно ``project_root``. По умолчанию —
            корень проекта.
        timeout: Таймаут в секундах (1..300, по умолчанию 30).
        project_root: Корень проекта (инжектируется через AgentContext).
        security: Политика безопасности (инжектируется через AgentContext).
            ``allow_arbitrary_shell=True`` разрешает произвольные команды
            (dangerous mode). ``None`` → ``SecurityPolicy()`` (безопасный).

    Returns:
        Строка с заголовком ``$ command``, выводом и кодом выхода.
    """
    if not command or not command.strip():
        return "Ошибка: пустая команда."

    sec = security or SecurityPolicy()

    # 1. Песочница: без project_root выполнение бессмысленно и опасно.
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

    # 2. PowerShell.
    ps_exe = _powershell_exe()
    if ps_exe is None:
        return (
            "Ошибка: PowerShell не найден в PATH. Установи PowerShell Core "
            "(pwsh) или включи Windows PowerShell (powershell.exe)."
        )

        # 3. Blacklist — ВСЕГДА, независимо от allowlist и dangerous mode.
    # `rm -rf /`, `format C:`, `shutdown /r`, `curl | iex` — разрушительные
    # команды; сообщение должно явно говорить «чёрным списком», даже если
    # они также не входят в allowlist. Пользователь сразу понимает причину.
    # В dangerous mode поведение не меняется (там blacklist и раньше
    # срабатывал через ветку 3a).
    hit = _check_blacklist(command)
    if hit is not None:
        logger.warning(f"run_shell_command: blacklist hit ({hit}): {command[:120]}")
        # `dangerous` в audit = был ли включён dangerous mode (не факт
        # разрушительности — она уже зафиксирована в result="blacklist").
        _audit(
            command,
            cwd,
            approved=False,
            result="blacklist",
            is_error=True,
            dangerous=sec.allow_arbitrary_shell,
        )
        return (
            "Ошибка: команда отклонена чёрным списком разрушительных "
            "операций (и не входит в allowlist безопасных shell-команд). "
            "Переформулируй задачу без удаления/recurse-force/"
            "записи в реестр/shutdown/pipe-to-iex."
        )

    # 4. Allowlist / dangerous mode.
    in_allowlist = _in_allowlist(command)
    dangerous = False
    if not in_allowlist:
        if not sec.allow_arbitrary_shell:
            logger.info(f"run_shell_command: blocked (not in allowlist): {command[:120]}")
            _audit(command, cwd, approved=False, result="not in allowlist",
                   is_error=True, dangerous=False)
            return (
                "Ошибка: команда не входит в allowlist безопасных shell-команд. "
                "Разрешены: Test-Path, Get-ChildItem, Get-Content, Select-String, "
                "Measure-Object, Get-Location, Resolve-Path, Get-FileHash, "
                "python -m pytest|py_compile|unittest|mypy|ruff, "
                "git status|diff|log|show|branch|rev-parse|describe, "
                "dotnet test, go test, npm test. "
                "Для произвольных команд включи dangerous mode в ⚙ → Безопасность."
            )
        dangerous = True

    # 5. Нормализация таймаута.
    try:
        t = int(timeout)
    except (TypeError, ValueError):
        t = 30
    t = max(1, min(t, MAX_TIMEOUT_S))

    logger.info(
        f"run_shell_command: cwd={work_dir}, timeout={t}s, "
        f"dangerous={dangerous}, cmd={command[:200]}"
    )

    # 6. Запуск.
    try:
        out, rc = await _run_powershell(ps_exe, command, work_dir, t)
    except FileNotFoundError as e:
        _audit(command, cwd, approved=True, result=str(e),
               is_error=True, dangerous=dangerous)
        return f"Ошибка: не удалось запустить {ps_exe}: {e}"
    except Exception as e:
        logger.exception("run_shell_command failed")
        _audit(command, cwd, approved=True, result=str(e),
               is_error=True, dangerous=dangerous)
        return f"Ошибка запуска команды: {e}"

    # 7. Усечение вывода.
    truncated = False
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS]
        truncated = True

    # 8. Audit log.
    _audit(command, cwd, approved=True, result=out,
           is_error=(rc != 0), dangerous=dangerous)

    # 9. Ответ.
    header = f"$ {command}\n(exit {rc}, cwd {work_dir.name})"
    if dangerous:
        header += " [DANGEROUS MODE]"
    body = out.rstrip() if out else "(пустой вывод)"
    footer = "\n[Вывод обрезан до 5000 символов]" if truncated else ""
    return f"{header}\n{body}{footer}"


def _resolve_cwd(cwd: str, project_root: str) -> Path | None:
    """Устаревшая обёртка для обратной совместимости тестов.

    Семантика: пустой ``cwd`` — корень проекта, иначе резолв относительно
    корня; выход за проект → None.
    """
    resolver = SandboxedPathResolver(project_root)
    return resolver.root if not cwd else resolver.resolve(cwd)