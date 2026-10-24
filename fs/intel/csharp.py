"""C# / .NET / WinForms анализатор под реальный LocalMessenger_30.

Из дампа видно устойчивые сигнатуры, которые мы ИЗВЛЕКАЕМ детерминированно,
чтобы 7B не галлюцинировал:
  - namespace LocalMessenger.{Helpers,Network,Forms,...}
  - Program.Main -> Application.Run(new MainForm())  (+ кастомные readiness-гейты
    вида StartupReady, упомянутые в паспорте исследования)
  - static-классы-сервисы: KeyManager, CryptoService, Paths, Logger, Preflight,
    Theme, PasswordVault, HistoryManager, MessageBufferManager
  - partial MainForm (распил по файлам) -> признак незавершённой декомпозиции
  - .csproj: TargetFramework v4.6, OutputType, Reference/ProjectReference
  - MSTest-проект LocalMessenger.Tests (9/9) -> test-сигнал
  - AGENTS.md/PLAN.md: веточные конвенции + архитектурный roadmap (Core/Network/UI)
"""

from __future__ import annotations

import re
from pathlib import Path

from fs.intel import FileRecord, Symbol

_NS_RE = re.compile(r"^\s*namespace\s+([\w.]+)", re.M)
_CLASS_RE = re.compile(
    r"^\s*(?:(?:public|internal|private|protected|sealed|static|abstract|partial)\s+)*"
    r"(class|interface|struct|enum|record)\s+(\w+)",
    re.M,
)
_USING_RE = re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", re.M)
_MAIN_RE = re.compile(r"\bstatic\s+(?:async\s+)?(?:int|void|Task)\s+Main\s*\(", re.I)
_APPRUN_RE = re.compile(r"Application\.Run\s*\(\s*new\s+(\w+)")
_STATTHREAD_RE = re.compile(r"\[STAThread\]")
_READY_GATE_RE = re.compile(
    r"\b(?:public|internal|static)\s+(?:bool|Task<bool>)\s+(\w*(?:Ready|Gate|Startup)\w*)\b", re.I
)
_CSPROJ_TFM = re.compile(r"<TargetFramework(?:s)?>([^<]+)</TargetFramework(?:s)?>", re.I)
_CSPROJ_OUT = re.compile(r"<OutputType>([^<]+)</OutputType>", re.I)
_CSPROJ_START = re.compile(r"<StartupObject>([^<]+)</StartupObject>", re.I)
_CSPROJ_REF = re.compile(r"<(?:Project)?Reference\s+(?:Include|Update)=\"([^\"]+)\"", re.I)
_PARTIAL_RE = re.compile(r"\bpartial\s+(?:class|struct)\s+(\w+)")

# ---------------------------------------------------------------
# Извлечение тела метода и вызовов (call-graph без type-checker)
# ---------------------------------------------------------------

# `new ClassName(` — создание объекта. Самая надёжная эвристика.
_CALL_NEW_RE = re.compile(r"\bnew\s+([A-Z]\w+)\s*[({]")
# `X.MethodName(` — вызов метода у произвольного объекта.
_CALL_METHOD_RE = re.compile(r"\.([A-Za-z_]\w*)\s*\(")
# `MethodName(` — вызов статического метода без префикса (редко, шумно).
_CALL_BARE_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,})\s*\(")

# Имена из BCL/фреймворка и коллекционных методов — не архитектурные вызовы.
_BUILTIN_METHODS = frozenset({
    "ToString", "Equals", "GetHashCode", "GetType", "ReferenceEquals",
    "Add", "AddRange", "Remove", "RemoveAt", "RemoveAll", "Contains",
    "ContainsKey", "ContainsValue", "Clear", "Count", "Any", "All",
    "First", "FirstOrDefault", "Last", "LastOrDefault", "Where", "Select",
    "SelectMany", "ToArray", "ToList", "ToDictionary", "ForEach",
    "Trim", "TrimStart", "TrimEnd", "Split", "Replace", "Substring",
    "StartsWith", "EndsWith", "IndexOf", "LastIndexOf", "ToLower", "ToUpper",
    "Write", "WriteLine", "Read", "ReadLine", "Flush", "Dispose", "Close",
    "Show", "ShowDialog", "Hide", "Invoke", "BeginInvoke", "EndInvoke",
    "Format", "Parse", "TryParse", "Concat", "Join",
    "GetString", "GetBytes", "GetBuffer", "GetLength",
})


def _extract_method_body(text: str, from_pos: int, max_len: int = 4000) -> str:
    """Возвращает тело `{ ... }`, начиная с первой `{` после from_pos.

    Небольшой баланс-парсер: учитывает вложенные скобки и строковые
    литералы. Ограничен max_len символов, чтобы не читать многострочный
    файл в память повторно.

    Args:
        text: Содержимое файла.
        from_pos: Позиция, с которой ищем открывающую скобку.
        max_len: Максимальная длина тела.

    Returns:
        Тело метода (включая скобки) или пустая строка.
    """
    open_brace = text.find("{", from_pos)
    if open_brace < 0:
        return ""
    end_limit = min(len(text), open_brace + max_len)
    depth = 0
    i = open_brace
    in_str: str | None = None
    while i < end_limit:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace : i + 1]
        i += 1
    return text[open_brace:end_limit]


def _extract_calls(body: str) -> list[str]:
    """Извлекает имена вызываемых сущностей из тела метода.

    Возвращает **короткие имена** (без namespace) — сопоставление
    с символами делает `DependencyGraph._link_calls`. Приоритет:
      * `new X(...)` — конструктор (надёжно);
      * `X.Method(...)` — метод у объекта;
      * `Method(...)` — статический вызов (эвристика, фильтруется).

    Args:
        body: Тело метода (результат `_extract_method_body`).

    Returns:
        Уникальные имена, отсортированные по алфавиту.
    """
    calls: set[str] = set()
    for m in _CALL_NEW_RE.finditer(body):
        calls.add(m.group(1))
    for m in _CALL_METHOD_RE.finditer(body):
        name = m.group(1)
        if name in _BUILTIN_METHODS or len(name) < 3:
            continue
        calls.add(name)
    for m in _CALL_BARE_RE.finditer(body):
        name = m.group(1)
        if name in _BUILTIN_METHODS or len(name) < 4:
            continue
        # Исключаем ключевые слова/типы.
        if name in {"String", "Object", "Exception", "Task", "List", "Dictionary"}:
            continue
        calls.add(name)
    return sorted(calls)

def parse_csharp_file(p: Path, rec: FileRecord):
    """-> (symbols, domain_piece). Домен-пьеc для .csproj/AGENTS.md."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], None
    if rec.language == "msbuild":  # .csproj
        return [], _parse_csproj(text, rec)
    if rec.language == "solution":  # .sln
        return [], _parse_sln(text, rec)
    if rec.language == "markdown":  # AGENTS.md / PLAN.md / README
        return [], _parse_md_contract(text, rec)
    if not rec.is_source:
        return [], None

    ns = _NS_RE.search(text)
    namespace = ns.group(1) if ns else rec.namespace
    syms: list[Symbol] = []
    for m in _CLASS_RE.finditer(text):
        kind, name = m.group(1), m.group(2)
        line = text.count("\n", 0, m.start()) + 1
        mods = _modifiers(text, m.start())
        syms.append(
            Symbol(
                qualified_name=f"{namespace}.{name}" if namespace else name,
                kind=kind,
                file=rec.path,
                line=line,
                signature=f"{mods}{kind} {name}",
                metadata={"namespace": namespace, "partial": "partial" in mods, "static": "static" in mods},
            )
        )
    # Методы верхнего уровня класса (по индентации внутри class-блока — эвристика).
    # Позиции классов — нужны, чтобы каждому методу приписать владельца.
    class_positions = [(cm.start(), cm.group(2)) for cm in _CLASS_RE.finditer(text)]

    def _owner_class_at(pos: int) -> str | None:
        owner: str | None = None
        for cstart, cname in class_positions:
            if cstart < pos:
                owner = cname
            else:
                break
        return owner

    for m in re.finditer(
        r"^\s*(?:(?:public|internal|private|protected|static|async|virtual|override|sealed)\s+)+"
        r"[\w<>\[\],.\s?]+?\s+(\w+)\s*\([^)]*\)\s*(?:=>|\{)",
        text,
        re.M,
    ):
        name = m.group(1)
        line = text.count("\n", 0, m.start()) + 1

        # NEW: тело метода → список вызываемых имён (call-graph).
        body = _extract_method_body(text, m.end())
        calls = _extract_calls(body) if body else []

        owner_class = _owner_class_at(m.start())
        metadata: dict = {"calls": calls}
        if owner_class:
            metadata["owner_class"] = owner_class

        syms.append(
            Symbol(
                qualified_name=f"{namespace}::{name}" if namespace else name,
                kind="method",
                file=rec.path,
                line=line,
                signature=m.group(0).strip()[:160],
                metadata=metadata,
            )
        )
    # Entry-point сигнатуры.
    entry_meta: dict = {}
    if _MAIN_RE.search(text):
        entry_meta["has_main"] = True
        run = _APPRUN_RE.search(text)
        if run:
            entry_meta["launches"] = run.group(1)
        entry_meta["stat_thread"] = bool(_STATTHREAD_RE.search(text))
        gates = _READY_GATE_RE.findall(text)
        if gates:
            entry_meta["readiness_gates"] = gates  # StartupReady и т.п.
    partials = _PARTIAL_RE.findall(text)
    if partials:
        for nm in set(partials):
            for s in syms:
                if s.qualified_name.endswith("." + nm) or s.qualified_name == nm:
                    s.metadata["partial_group"] = nm
    usings = sorted(set(_USING_RE.findall(text)))
    if usings:
        for s in syms:
            s.metadata.setdefault("usings", usings)
    domain_piece = (
        {
            "kind": "csharp_file",
            "path": rec.path,
            "namespace": namespace,
            "usings": usings,
            "entry": entry_meta or None,
            "partials": sorted(set(partials)),
        }
        if (entry_meta or partials or usings)
        else None
    )
    return syms, domain_piece


def _modifiers(text: str, pos: int) -> str:
    line_start = text.rfind("\n", 0, pos) + 1
    head = text[line_start:pos]
    return " ".join(
        w
        for w in head.split()
        if w
        in {
            "public",
            "internal",
            "private",
            "protected",
            "sealed",
            "static",
            "abstract",
            "partial",
            "readonly",
        }
    ) + (" " if head.split() else "")


def _parse_csproj(text: str, rec: FileRecord) -> dict:
    tfm = _CSPROJ_TFM.search(text)
    out = _CSPROJ_OUT.search(text)
    start = _CSPROJ_START.search(text)
    refs = _CSPROJ_REF.findall(text)
    return {
        "kind": "csproj",
        "path": rec.path,
        "target_framework": tfm.group(1).strip() if tfm else None,
        "output_type": out.group(1).strip() if out else None,
        "startup_object": start.group(1).strip() if start else None,
        "references": refs,
    }


def _parse_sln(text: str, rec: FileRecord) -> dict:
    projects = re.findall(r'Project\("\{[^}]+\}"\)\s*=\s*"([^"]+)"\s*,\s*"([^"]+)"', text)
    return {"kind": "sln", "path": rec.path, "projects": [{"name": n, "rel": r} for n, r in projects]}


def _parse_md_contract(text: str, rec: FileRecord) -> dict:
    """AGENTS.md/PLAN.md -> структурный контракт для аналитика (не прозу модели)."""
    branches = re.findall(r"`?(feature|fix|bugfix|hotfix|develop|main|master|minify|fork)`?", text, re.I)
    roadmap_h = re.findall(
        r"^#{1,4}\s*(Архитектурный roadmap|Roadmap|Декомпозиция|Уровень\s*\d+.*|Сделано.*)$",
        text,
        re.M | re.I,
    )
    todos = re.findall(r"^\s*[-*]\s*\[[ x]\]\s*(.+)$", text, re.M)
    risks = re.findall(r"(не завершён|декомпозиция|mёртв|закомментирован|TODO|FIXME|HACK)", text, re.I)
    return {
        "kind": "contract_md",
        "path": rec.path,
        "branch_conventions": sorted(set(b.lower() for b in branches)),
        "roadmap_headers": roadmap_h,
        "open_tasks": todos[:40],
        "risk_keywords": sorted(set(r.lower() for r in risks)),
    }
