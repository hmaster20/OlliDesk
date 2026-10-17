"""Диспетчер парсеров: tree-sitter для языков + доменные анализаторы для IaC.

Уровни деградации (важно для офлайна и слабых машин):
  A. tree-sitter grammar  -> точный AST
  B. stdlib/regex-heuristic (Python ast, C#/Ruby/HCL regex) -> базовая карта
  C. только FileRecord (язык по расширению/имени) -> минимум
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from fs.intel import FileRecord, Symbol

# Файлы БЕЗ расширения, определяемые по имени (D6).
_NAME_LANG: dict[str, str] = {
    "vagrantfile": "vagrant", "rakefile": "ruby", "gemfile": "ruby",
    "guardfile": "ruby", "podfile": "ruby", "dockerfile": "docker",
    "makefile": "make", "playbook": "ansible", "site.yml": "ansible",
    "build.gradle": "groovy", "cmakelists.txt": "cmake",
}
_EXT_LANG: dict[str, str] = {
    ".py": "python", ".pyi": "python", ".cs": "csharp", ".csx": "csharp",
    ".csproj": "msbuild", ".sln": "solution", ".rb": "ruby", ".tf": "terraform",
    ".tfvars": "terraform-vars", ".hcl": "hcl", ".pkr.hcl": "hcl",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".toml": "toml",
    ".go": "go", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".html": "html", ".css": "css", ".md": "markdown",
    ".xml": "xml", ".config": "xml", ".resx": "xml", ".razor": "csharp",
    ".sh": "shell", ".bash": "shell", ".sql": "sql", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".php": "php",
}
_SOURCE_EXT = {".py", ".cs", ".rb", ".go", ".js", ".ts", ".tsx", ".jsx", ".rs", ".java", ".kt", ".php", ".tf", ".hcl"}
_CONFIG_EXT = {".csproj", ".sln", ".toml", ".json", ".yaml", ".yml", ".config", ".xml", ".tfvars"}
_DOC_EXT = {".md", ".rst", ".txt"}
_TEST_HINT = re.compile(r"(?:^|[/\\])(?:tests?|spec[s]?|test_[^/\\]+|[^/\\]+_test\.(?:go|py)|[^/\\]+\.tests?\.cs)(?:$|[./\\])", re.I)


def detect_language(p: Path) -> str:
    name = p.name.lower()
    if name in _NAME_LANG:
        return _NAME_LANG[name]
    ext = p.suffix.lower()
    if ext in _EXT_LANG:
        return _EXT_LANG[ext]
    if name.endswith(".yml") or name.endswith(".yaml"):
        # Ansible-плейбуки/роли узнаются по содержимому в доменном парсере.
        return "yaml"
    return "unknown"


def _grammar_or_none(lang: str):
    """Ленивая загрузка tree-sitter grammar; None => уровень B/C."""
    try:
        import tree_sitter as ts
        mapping = {
            "python": "tree_sitter_python", "csharp": "tree_sitter_c_sharp",
            "ruby": "tree_sitter_ruby", "vagrant": "tree_sitter_ruby",
            "yaml": "tree_sitter_yaml", "go": "tree_sitter_go",
            "javascript": "tree_sitter_javascript", "typescript": "tree_sitter_typescript",
        }
        mod_name = mapping.get(lang)
        if not mod_name:
            return None
        mod = __import__(mod_name)
        lang_obj = ts.Language(mod.language())
        return ts.Parser(lang_obj)
    except Exception:
        return None


def detect_and_parse_all(root: Path, *, indexer=None):
    """Обход + диспетчинг. Возвращает (files, symbols, manifests, entries, domain)."""
    files: list[FileRecord] = []
    symbols: list[Symbol] = []
    manifests: list[str] = []
    entries: list[str] = []
    domain: dict[str, Any] = {}

    ignored = _load_ignores(root)
    for p in _walk(root, ignored):
        lang = detect_language(p)
        rec = _make_record(p, root, lang)
        files.append(rec)
        if rec.is_config:
            manifests.append(rec.path)
        if rec.is_entry_candidate:
            entries.append(rec.path)
        # Диспетчинг по языку/домену.
        syms, dom_piece = _parse_by_lang(p, lang, rec)
        symbols.extend(syms)
        if dom_piece:
            domain.setdefault(lang if lang in {"ansible","terraform","vagrant","hcl"} else "misc", [])
            if isinstance(domain.get(lang if lang in {"ansible","terraform","vagrant"} else "misc"), list):
                domain.setdefault("roles", []); domain.setdefault("modules", []); domain.setdefault("boxes", [])
            _merge_domain(domain, lang, dom_piece)
    # Доменные сборщики (Ansible/Terraform/Vagrant) над全体 файлами.
    from fs.intel.ansible import build_ansible_project
    from fs.intel.terraform import build_terraform_project
    from fs.intel.vagrant import build_vagrant_project
    domain["ansible"] = build_ansible_project(root, files)
    domain["terraform"] = build_terraform_project(root, files)
    domain["vagrant"] = build_vagrant_project(root, files)
    # Entry-points дополняем доменными маркерами (D7).
    entries = sorted(set(entries) | _domain_entries(domain))
    return files, symbols, manifests, entries, domain


def _parse_by_lang(p: Path, lang: str, rec: FileRecord):
    """Выбор парсера: tree-sitter -> доменный -> regex-heuristic."""
    if lang == "csharp":
        from fs.intel.csharp import parse_csharp_file
        return parse_csharp_file(p, rec)
    if lang == "python":
        from fs.intel.python import parse_python_file
        return parse_python_file(p, rec)
    if lang in {"vagrant", "ruby"}:
        from fs.intel.vagrant import parse_ruby_file
        return parse_ruby_file(p, rec, vagrant=(lang == "vagrant"))
    if lang in {"terraform", "hcl", "terraform-vars"}:
        from fs.intel.terraform import parse_hcl_file
        return parse_hcl_file(p, rec)
    if lang in {"yaml", "ansible"}:
        from fs.intel.ansible import parse_yaml_file
        return parse_yaml_file(p, rec)
    # generic tree-sitter для остальных
    parser = _grammar_or_none(lang)
    if parser is not None and rec.is_source:
        return _ts_generic(parser, p, lang, rec), None
    return [], None


def _ts_generic(parser, p: Path, lang: str, rec: FileRecord) -> list[Symbol]:
    try:
        src = p.read_bytes()
    except OSError:
        return []
    tree = parser.parse(src)
    out: list[Symbol] = []
    KIND_NODES = {
        "class_definition": "class", "function_definition": "function",
        "method_definition": "method", "interface_declaration": "interface",
        "struct_declaration": "struct", "enum_declaration": "enum",
        "object_declaration": "object", "type_alias_declaration": "type",
    }
    def walk(node):
        if node.type in KIND_NODES:
            name = _child_text(node, "name") or _child_text(node, "identifier") or "?"
            sig = _first_line(src, node.start_byte)
            out.append(Symbol(qualified_name=name, kind=KIND_NODES[node.type],
                              file=rec.path, line=node.start_point[0] + 1, signature=sig))
        for ch in node.children:
            walk(ch)
    walk(tree.root_node)
    return out


def _child_text(node, type_or_field: str) -> str | None:
    try:
        ch = node.child_by_field_name(type_or_field)
        if ch is not None:
            return ch.text.decode("utf-8", "replace")
    except Exception:
        pass
    for c in node.children:
        if c.type == type_or_field:
            return c.text.decode("utf-8", "replace")
    return None


def _first_line(src: bytes, byte_off: int) -> str:
    nl = src.find(b"\n", byte_off)
    end = nl if nl != -1 else len(src)
    return src[byte_off:end].decode("utf-8", "replace").strip()[:160]


def _make_record(p: Path, root: Path, lang: str) -> FileRecord:
    rel = p.relative_to(root).as_posix()
    try:
        st = p.stat()
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0.0
    import hashlib
    sha = ""
    if size and size <= 2_000_000:
        try:
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            sha = ""
    ext = p.suffix.lower()
    is_doc = ext in _DOC_EXT or lang == "markdown"
    is_cfg = ext in _CONFIG_EXT or lang in {"msbuild", "solution", "toml", "json", "yaml", "xml"}
    is_src = (ext in _SOURCE_EXT or lang in {"vagrant", "ansible", "terraform", "hcl"}) and not is_doc
    is_test = bool(_TEST_HINT.search(rel)) or rel.endswith(("_test.go", "test.py")) or ".Tests." in rel or "/Tests/" in rel
    is_entry = _is_entry(rel, lang)
    return FileRecord(path=rel, language=lang, size=size, mtime=mtime, sha256=sha,
                      is_source=is_src, is_config=is_cfg, is_doc=is_doc,
                      is_test=is_test, is_entry_candidate=is_entry,
                      module_name=_module_of(rel, lang), namespace=_ns_of(rel, lang),
                      package=_pkg_of(rel, lang))


_ENTRY_BASENAMES = {
    "main.py", "app.py", "cli.py", "__main__.py", "wsgi.py", "asgi.py",
    "manage.py", "program.cs", "startup.cs", "main.go", "main.rb",
    "config.ru", "vagrantfile", "site.yml", "playbook.yml", "main.tf",
    "index.ts", "index.js", "server.py", "run.py",
}
_ENTRY_DIRHINT = re.compile(r"(?:^|/)(?:src|app|cmd|entrypoint|bootstrap)/", re.I)


def _is_entry(rel: str, lang: str) -> bool:
    base = rel.rsplit("/", 1)[-1].lower()
    if base in _ENTRY_BASENAMES:
        return True
    if lang == "msbuild" and base.endswith(".csproj"):
        return True   # манифест сборки = кандидат точки входа для .NET
    if _ENTRY_DIRHINT.search(rel) and lang in {"python", "csharp", "go", "ruby", "typescript", "javascript"}:
        return True
    return False


def _module_of(rel: str, lang: str) -> str | None:
    stem = rel.rsplit("/", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    return stem if lang in {"python", "csharp", "go", "ruby", "javascript", "typescript"} else None


def _ns_of(rel: str, lang: str) -> str | None:
    if lang == "csharp":
        parts = rel.split("/")
        # LocalMessenger/Helpers/X.cs -> LocalMessenger.Helpers
        if len(parts) >= 2:
            return ".".join(p for p in parts[:-1] if p and not p.endswith(".csproj"))
    return None


def _pkg_of(rel: str, lang: str) -> str | None:
    if lang == "go":
        return rel.rsplit("/", 1)[0] or "."
    return None


def _load_ignores(root: Path) -> list[str]:
    pats = [".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
            "build", ".env", "*.pyc", "obj", "bin", ".idea", ".vs", ".ollidesk",
            "packages", "TestResults", "*.min.js", ".terraform", "*.tfstate*"]
    for fname in (".gitignore", ".indexignore"):
        f = root / fname
        if f.exists():
            try:
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pats.append(line)
            except OSError:
                pass
    return pats


def _walk(root: Path, ignored: list[str]):
    skip_dirs = set()
    for pat in ignored:
        if "/" not in pat and "*" not in pat and "." not in pat.lstrip("."):
            skip_dirs.add(pat)
    for dirpath, dirnames, filenames in __import__("os").walk(root):
        dp = Path(dirpath)
        rel_dp = dp.relative_to(root).as_posix()
        dirnames[:] = [d for d in dirnames
                       if d not in skip_dirs and not any(fnmatch.fnmatch(f"{rel_dp}/{d}", p) for p in ignored)]
        for fn in filenames:
            fp = dp / fn
            rel = fp.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(fn, p) for p in ignored):
                continue
            yield fp


def _merge_domain(domain: dict, lang: str, piece: Any) -> None:
    if not piece:
        return
    if lang in {"ansible"} and isinstance(piece, dict):
        domain.setdefault("ansible_partial", []).append(piece)
    elif lang in {"terraform", "hcl"} and isinstance(piece, dict):
        domain.setdefault("terraform_partial", []).append(piece)
    elif lang in {"vagrant", "ruby"} and isinstance(piece, dict):
        domain.setdefault("vagrant_partial", []).append(piece)


def _domain_entries(domain: dict) -> list[str]:
    out: list[str] = []
    ans = domain.get("ansible") or {}
    out += [p["path"] for p in ans.get("playbooks", [])]
    tf = domain.get("terraform") or {}
    out += tf.get("root_files", [])
    vg = domain.get("vagrant") or []
    out += [v["path"] for v in vg]
    return out