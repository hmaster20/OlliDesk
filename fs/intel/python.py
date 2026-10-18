"""Python-анализатор под реальный machine_138 (PySide6-трекер с офлайн-гейтом).

Извлекает доменные сигнатуры, которые иначе 7B не увидит:
  - сетевые сервисы с офлайн-защитой: require_online()/NetworkUnavailableError,
    mark_success/mark_failure, requests/httpx + User-Agent, connect_timeout
  - фоновые воркеры: subclassing BaseWorker, signals data/error/finished,
    stop_check/on_progress, QTimer.singleShot отложенные апдейты
  - файловый мониторинг: QFileSystemWatcher + QTimer-страховка (гибрид)
  - слой БД: Database().fetch_all/fetch_one/execute/insert_raw_import/get_metadata
  - слой конфига: Config().get/get_int + секции [Network]/[ReportScanner]/[Panic]
  - ETL/импорт: SberImporter, ETLService, raw_imports/raw_tables, file_hash-репарсинг
Это даёт LLM готовый тезаурус «проект = GUI-трекер + фоновые сетевые загрузчики
с офлайн-гейтом + гибрид FS-мониторинга + SQLite-ETL», без чтения 10 файлов.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from fs.intel import FileRecord, Symbol

_NET_CLIENT_RE = re.compile(r"\b(requests|httpx|curl_cffi|aiohttp)\b")
_OFFLINE_GATE_RE = re.compile(r"\b(require_online|NetworkUnavailableError|mark_success|mark_failure)\b")
_QT_WORKER_RE = re.compile(r"\bBaseWorker\b|\bsignals\.(data|error|finished)\b|\bQTimer\.singleShot\b")
_FS_WATCH_RE = re.compile(r"\bQFileSystemWatcher\b")
_DB_CALL_RE = re.compile(r"\bself\.db\.(fetch_all|fetch_one|execute|insert_\w+|get_metadata|save_\w+)\b")
_CFG_CALL_RE = re.compile(r"\bConfig\(\)\.get(?:_int|_bool)?\(|\bself\.config\.get(?:_int|_bool)?\(")
_ETL_RE = re.compile(r"\b(ETLService|SberImporter|raw_imports|raw_tables|process_import|file_hash)\b")


def parse_python_file(p: Path, rec: FileRecord):
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], None
    syms: list[Symbol] = []
    meta: dict = {"flags": []}
    try:
        tree = ast.parse(src, filename=str(p))
    except SyntaxError:
        # Уровень B: regex-эвристика, если ast не смог (неполный файл и т.п.)
        return _regex_symbols(src, rec), _domain_piece(src, rec, partial=True)

    module_doc = ast.get_docstring(tree) or ""
    imports = _collect_imports(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = [_unparse(b) for b in node.bases]
            syms.append(
                Symbol(
                    qualified_name=node.name,
                    kind="class",
                    file=rec.path,
                    line=node.lineno,
                    signature=f"class {node.name}({', '.join(bases)})",
                    metadata={
                        "bases": bases,
                        "methods": [m.name for m in node.body if isinstance(m, ast.FunctionDef)],
                    },
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "function"
            syms.append(
                Symbol(
                    qualified_name=node.name,
                    kind=kind,
                    file=rec.path,
                    line=node.lineno,
                    signature=("async " if isinstance(node, ast.AsyncFunctionDef) else "")
                    + f"def {node.name}(...)",
                    metadata={"decorators": [_unparse(d) for d in node.decorator_list]},
                )
            )
    # Доменные флаги по всему файлу.
    if _OFFLINE_GATE_RE.search(src):
        meta["flags"].append("network_gated")
    if _NET_CLIENT_RE.search(src):
        meta["flags"].append("http_client")
    if _QT_WORKER_RE.search(src):
        meta["flags"].append("qt_background_worker")
    if _FS_WATCH_RE.search(src):
        meta["flags"].append("fs_watcher")
    if _DB_CALL_RE.search(src):
        meta["flags"].append("db_layer")
    if _CFG_CALL_RE.search(src):
        meta["flags"].append("config_layer")
    if _ETL_RE.search(src):
        meta["flags"].append("etl_import")
    if rec.is_test:
        meta["flags"].append("test")
    domain = _domain_piece(src, rec, module_doc=module_doc, imports=imports, flags=meta["flags"])
    return syms, domain


def _collect_imports(tree: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out += [f"{mod}.{a.name}" for a in node.names]
    return sorted(set(out))


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return getattr(node, "id", getattr(node, "attr", "?"))


def _regex_symbols(src: str, rec: FileRecord) -> list[Symbol]:
    out: list[Symbol] = []
    for m in re.finditer(r"^\s*class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:", src, re.M):
        out.append(
            Symbol(
                qualified_name=m.group(1),
                kind="class",
                file=rec.path,
                line=src.count("\n", 0, m.start()) + 1,
                signature=m.group(0).strip()[:160],
                metadata={"bases": [b.strip() for b in (m.group(2) or "").split(",") if b.strip()]},
            )
        )
    for m in re.finditer(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", src, re.M):
        out.append(
            Symbol(
                qualified_name=m.group(1),
                kind="function",
                file=rec.path,
                line=src.count("\n", 0, m.start()) + 1,
                signature=m.group(0).strip()[:160],
            )
        )
    return out


def _domain_piece(
    src: str,
    rec: FileRecord,
    *,
    module_doc: str = "",
    imports: list[str] | None = None,
    flags: list[str] | None = None,
    partial: bool = False,
) -> dict | None:
    fl = flags or []
    if not (fl or module_doc or imports):
        return None
    # Секции конфига, которые читает этот файл.
    sections = sorted(set(re.findall(r"\.get(?:_int|_bool)?\(\s*[\"'](\w+)[\"']", src)))
    return {
        "kind": "python_file",
        "path": rec.path,
        "doc": module_doc[:400],
        "imports_head": (imports or [])[:30],
        "flags": fl,
        "config_sections": sections,
        "partial_parse": partial,
    }
