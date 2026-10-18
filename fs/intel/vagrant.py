"""Vagrant/Ruby анализатор под реальный Vagrant_2.

Из дампа видно устойчивые конструкции, которые извлекаем детерминированно:
  - config.vm.provision :shell, inline: $var / path: "../../_shell/...sh" / env: {...}
  - цепочка provision-шагов = «архитектура» машины (apt_init -> tools -> docker -> nexus network)
  - env-переменные провижнов: PROXY, DNS_IP, DNS_NAME, master_ip
  - boxes / networks / synced_folders / vmware|virtualbox providers
  - ТЕХНИЧЕСКИЙ ДОЛГ: закомментированные provision-строки (история миграции
    inline->path) — видимый сигнал для аналитика/верификатора.
Ruby-подмножество парсится tree-sitter-ruby; при отсутствии — regex-heuristic.
"""

from __future__ import annotations

import re
from pathlib import Path

from fs.intel import FileRecord, Symbol

_PROV_INLINE = re.compile(
    r"\.vm\.provision\s*:?\s*shell\s*,?\s*:?\s*inline\s*:?=>?\s*(\$?\w+|\"[^\"]*\"|'.*?')", re.I
)
_PROV_PATH = re.compile(
    r"\.vm\.provision\s+[\"']shell[\"']\s*,\s*path:\s*[\"']([^\"']+)[\"'](?:\s*,\s*run:\s*[\"'](\w+)[\"'])?",
    re.I,
)
_PROV_ENV = re.compile(r"env:\s*\{([^}]*)\}", re.I)
_BOX = re.compile(r"\.vm\.box\s*=?\s*[\"']([^\"']+)[\"']|box:\s*[\"']([^\"']+)[\"']", re.I)
_NETWORK = re.compile(r"\.vm\.network\s+[\"'](\w+)[\"']\s*,\s*[\"']([^\"']+)[\"']", re.I)
_SYNCED = re.compile(r"\.vm\.synced_folder\s+[\"']([^\"']+)[\"']\s*,\s*[\"']([^\"']+)[\"']", re.I)
_PROVIDER = re.compile(r"\.vm\.provider\s*[:]?\s*(\w+)", re.I)
_COMMENTED_PROV = re.compile(r"^\s*#\s*.*\.vm\.provision", re.I | re.M)
_RB_CLASS = re.compile(r"^\s*class\s+(\w+)", re.M)
_RB_MODULE = re.compile(r"^\s*module\s+(\w+)", re.M)
_RB_DEF = re.compile(r"^\s*def\s+(\w+)", re.M)


def parse_ruby_file(p: Path, rec: FileRecord, *, vagrant: bool):
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], None
    syms: list[Symbol] = []
    for m in _RB_CLASS.finditer(text):
        syms.append(
            Symbol(
                qualified_name=m.group(1),
                kind="class",
                file=rec.path,
                line=text.count("\n", 0, m.start()) + 1,
                signature=m.group(0).strip(),
            )
        )
    for m in _RB_MODULE.finditer(text):
        syms.append(
            Symbol(
                qualified_name=m.group(1),
                kind="module",
                file=rec.path,
                line=text.count("\n", 0, m.start()) + 1,
                signature=m.group(0).strip(),
            )
        )
    for m in _RB_DEF.finditer(text):
        syms.append(
            Symbol(
                qualified_name=m.group(1),
                kind="method",
                file=rec.path,
                line=text.count("\n", 0, m.start()) + 1,
                signature=m.group(0).strip(),
            )
        )
    if not vagrant:
        return syms, None
    prov_chain = _provision_chain(text)
    env_vars = sorted(
        set(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", " ".join(e.group(1) for e in _PROV_ENV.finditer(text))))
    )
    commented = len(_COMMENTED_PROV.findall(text))
    domain = {
        "kind": "vagrantfile",
        "path": rec.path,
        "boxes": sorted(set(x for m in _BOX.finditer(text) for x in m.groups() if x)),
        "networks": [{"type": m.group(1), "arg": m.group(2)} for m in _NETWORK.finditer(text)],
        "synced_folders": [{"host": m.group(1), "guest": m.group(2)} for m in _SYNCED.finditer(text)],
        "providers": sorted(set(m.group(1).lower() for m in _PROVIDER.finditer(text))),
        "provision_chain": prov_chain,
        "env_vars": env_vars,
        "commented_provisions": commented,  # техдолг-сигнал
    }
    return syms, domain


def _provision_chain(text: str) -> list[dict]:
    """Упорядоченная цепочка провижнов (line-order) — это и есть «архитектура» VM."""
    items: list[tuple[int, dict]] = []
    for m in _PROV_PATH.finditer(text):
        items.append(
            (m.start(), {"type": "shell", "mode": "path", "target": m.group(1), "run": m.group(2) or "once"})
        )
    for m in _PROV_INLINE.finditer(text):
        snippet = m.group(1)
        items.append(
            (
                m.start(),
                {
                    "type": "shell",
                    "mode": "inline",
                    "target": snippet[:80],
                    "is_var": snippet.startswith("$") or snippet.startswith("@"),
                },
            )
        )
    items.sort(key=lambda t: t[0])
    return [it for _, it in items]


def build_vagrant_project(root: Path, files: list[FileRecord]) -> list[dict]:
    """Собирает все Vagrantfile проекта (монорепо: несколько машин)."""
    out: list[dict] = []
    for f in files:
        if f.language == "vagrant":
            p = root / f.path
            _, dom = parse_ruby_file(p, f, vagrant=True)
            if dom:
                out.append(dom)
    return out
