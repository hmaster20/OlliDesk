"""Ansible-анализатор по канону (roles/*/meta/main.yml, tasks, handlers,
defaults, vars, templates, files; плейбуки site.yml/playbook.yml; inventory;
group_vars/host_vars; collections; vault). Дамп ansible_cloud_1.md пришёл
пустым — реализация по стандарту, интерфейс стабилен, калибровка на реальных
репо не меняет сигнатуры.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from fs.intel import FileRecord, Symbol

_ROLE_DIRS = {"tasks", "handlers", "defaults", "vars", "templates", "files", "meta", "library", "module_utils"}


def parse_yaml_file(p: Path, rec: FileRecord):
    """-> (symbols, domain_piece). Для yaml/ansible-файлов."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], None
    syms: list[Symbol] = []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = None
    if isinstance(data, list):
        for i, task in enumerate(data):
            if isinstance(task, dict):
                name = task.get("name") or task.get("-") or f"task#{i}"
                mods = [k for k in task if k in {"notify", "when", "loop", "with_items", "register", "tags", "handler"}]
                syms.append(Symbol(qualified_name=str(name)[:80], kind="task", file=rec.path,
                                   line=1, signature=f"{name} [{','.join(mods)}]",
                                   metadata={"modules": list(task.keys())[:10]}))
    elif isinstance(data, dict):
        for k in data:
            syms.append(Symbol(qualified_name=str(k), kind="var", file=rec.path, line=1,
                               signature=f"{k}: …"))
    domain = {"kind": "yaml_file", "path": rec.path, "shape": type(data).__name__,
              "is_playbook": _looks_like_playbook(data), "is_role_part": _role_part(rec.path)}
    return syms, domain


def _looks_like_playbook(data: Any) -> bool:
    return isinstance(data, list) and any(isinstance(x, dict) and ("hosts" in x or "roles" in x or "tasks" in x) for x in data)


def _role_part(path: str) -> str | None:
    parts = path.split("/")
    for i, seg in enumerate(parts):
        if seg in _ROLE_DIRS and i >= 1:
            return f"{parts[i-1]}/{seg}"
    return None


def build_ansible_project(root: Path, files: list[FileRecord]) -> dict:
    roles: list[dict] = []
    playbooks: list[dict] = []
    inventories: list[str] = []
    group_vars: dict[str, list[str]] = {}
    host_vars: dict[str, list[str]] = {}
    collections: set[str] = set()
    vaults: list[str] = []

    role_index: dict[str, dict] = {}
    for f in files:
        parts = f.path.split("/")
        # roles/<name>/<part>/<file>
        if "roles" in parts:
            ri = parts.index("roles")
            if ri + 1 < len(parts):
                rname = parts[ri + 1]
                role = role_index.setdefault(rname, {"name": rname, "path": "/".join(parts[:ri + 2]),
                                                     "tasks": [], "handlers": [], "defaults": [],
                                                     "vars": [], "templates": [], "files": [],
                                                     "meta_dependencies": []})
                if ri + 2 < len(parts):
                    part = parts[ri + 2]
                    if part in {"tasks", "handlers", "defaults", "vars", "templates", "files"}:
                        role[part].append(f.path)
                    if part == "meta" and f.path.endswith(("main.yml", "main.yaml")):
                        role["meta_dependencies"] = _read_meta_deps(root / f.path)
                continue
        if f.language in {"ansible", "yaml"} and _is_playbook_path(f.path):
            playbooks.append({"path": f.path})
        if "inventory" in f.path.lower() or f.path.endswith((".ini",)) and "host" in f.path.lower():
            inventories.append(f.path)
        if "group_vars" in parts:
            gi = parts.index("group_vars")
            grp = parts[gi + 1] if gi + 1 < len(parts) else "_all"
            group_vars.setdefault(grp, []).append(f.path)
        if "host_vars" in parts:
            hi = parts.index("host_vars")
            h = parts[hi + 1] if hi + 1 < len(parts) else "_"
            host_vars.setdefault(h, []).append(f.path)
        if "$ANSIBLE_VAULT" in _head(root / f.path):
            vaults.append(f.path)
        for c in re.findall(r"\b(?:ansible\.|community\.|community\.general)\w*", _head(root / f.path)):
            collections.add(c)
    roles = list(role_index.values())
    # Рёбра role->role из meta dependencies.
    edges = []
    for r in roles:
        for dep in r["meta_dependencies"]:
            edges.append({"from": r["name"], "to": dep, "kind": "depends_on"})
    return {"roles": roles, "playbooks": playbooks, "inventories": inventories,
            "group_vars": group_vars, "host_vars": host_vars,
            "collections": sorted(collections), "vault_files": vaults,
            "role_edges": edges}


def _read_meta_deps(p: Path) -> list[str]:
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    deps = (data or {}).get("dependencies", []) if isinstance(data, dict) else []
    out: list[str] = []
    for d in deps:
        if isinstance(d, str):
            out.append(d)
        elif isinstance(d, dict) and "role" in d:
            out.append(str(d["role"]))
    return out


def _is_playbook_path(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    return base in {"site.yml", "site.yaml", "playbook.yml", "playbook.yaml"} or path.startswith("playbooks/")


def _head(p: Path, n: int = 4000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:n]
    except OSError:
        return ""