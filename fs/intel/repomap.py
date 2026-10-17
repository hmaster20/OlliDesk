"""Adaptive Repo-Map: трёхуровневая карта с бюджетом токенов.

L0 (всегда): дерево каталогов глубиной 2-3 с [N файлов · доминирующий язык].
L1 (architecture): по значимым каталогам top-K символов (K=3-6), ранжированных
   graph.score. Для 43 файлов покрывает всё; для 1000 — режется по top-N каталогов.
L2 (lazy, 0 токенов в базе): полные символы/файлы — только по запросу через
   read_symbol / RAG / инструменты.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from fs.intel import FileRecord, Symbol
from fs.intel.graph import DependencyGraph


@dataclass
class RepoMap:
    level0: str = ""
    level1: str = ""
    entries: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    languages: dict[str, int] = field(default_factory=dict)
    domain_summary: str = ""

    def as_prompt_block(self) -> str:
        parts = []
        if self.level0:
            parts.append("## L0 дерево\n" + self.level0)
        if self.level1:
            parts.append("## L1 символы (top)\n" + self.level1)
        if self.entries:
            parts.append("## Точки входа: " + ", ".join(self.entries[:8]))
        if self.manifests:
            parts.append("## Манифесты: " + ", ".join(self.manifests[:8]))
        if self.languages:
            top = sorted(self.languages.items(), key=lambda kv: -kv[1])[:6]
            parts.append("## Языки: " + ", ".join(f"{k}={v}" for k, v in top))
        if self.domain_summary:
            parts.append("## Домен\n" + self.domain_summary)
        return "\n\n".join(parts)


def build_repomap(files: list[FileRecord], symbols: list[Symbol],
                  entries: list[str], manifests: list[str],
                  domain: dict, *, graph: DependencyGraph | None = None,
                  l1_top_dirs: int = 12, l1_k: int = 5) -> RepoMap:
    langs: dict[str, int] = defaultdict(int)
    by_dir: dict[str, list[FileRecord]] = defaultdict(list)
    for f in files:
        langs[f.language] += 1
        d = str(PurePosixPath(f.path).parent)
        by_dir[d].append(f)
    level0 = _render_l0(by_dir, files)
    level1 = _render_l1(by_dir, symbols, graph, l1_top_dirs=l1_top_dirs, l1_k=l1_k)
    domain_summary = _render_domain(domain)
    return RepoMap(level0=level0, level1=level1, entries=sorted(set(entries)),
                   manifests=sorted(set(manifests)), languages=dict(langs),
                   domain_summary=domain_summary)


def _render_l0(by_dir: dict[str, list[FileRecord]], files: list[FileRecord]) -> str:
    # Группа по первым двум сегментам пути.
    groups: dict[str, list[FileRecord]] = defaultdict(list)
    for f in files:
        segs = PurePosixPath(f.path).parts
        key = "/".join(segs[:2]) if len(segs) >= 2 else (segs[0] if segs else ".")
        groups[key].append(f)
    lines = []
    for key in sorted(groups, key=lambda k: -len(groups[k])):
        fs_ = groups[key]
        lang = _dominant_lang(fs_)
        lines.append(f"{key}/  [{len(fs_)} файлов · {lang}]")
    return "\n".join(lines[:40])


def _dominant_lang(fs_: list[FileRecord]) -> str:
    c: dict[str, int] = defaultdict(int)
    for f in fs_:
        c[f.language] += 1
    return max(c, key=c.get) if c else "?"


def _render_l1(by_dir, symbols, graph, *, l1_top_dirs: int, l1_k: int) -> str:
    sym_by_file: dict[str, list[Symbol]] = defaultdict(list)
    for s in symbols:
        sym_by_file[s.file].append(s)
    # Ранжируем каталоги по сумме scores их символов (или по числу файлов).
    dir_score: dict[str, float] = defaultdict(float)
    for d, fs_ in by_dir.items():
        sc = 0.0
        for f in fs_:
            for s in sym_by_file.get(f.path, []):
                if graph:
                    nid = f"{s.kind}:{s.file}::{s.qualified_name}"
                    node = graph.nodes.get(nid)
                    sc += node.score if node else 0.0
                else:
                    sc += 1.0
        dir_score[d] = sc
    top_dirs = sorted(dir_score, key=lambda d: -dir_score[d])[:l1_top_dirs]
    lines = []
    for d in top_dirs:
        items: list[tuple[float, Symbol]] = []
        for f in by_dir[d]:
            for s in sym_by_file.get(f.path, []):
                if s.kind in {"class", "interface", "role", "module_tf", "resource", "box", "function", "method", "task", "provisioner"}:
                    score = 0.0
                    if graph:
                        node = graph.nodes.get(f"{s.kind}:{s.file}::{s.qualified_name}")
                        score = node.score if node else 0.0
                    items.append((score, s))
        items.sort(key=lambda t: -t[0])
        if not items:
            continue
        lines.append(f"{d}/")
        for _, s in items[:l1_k]:
            lines.append(f"  {s.kind} {s.qualified_name}  ({f.path.split('/')[-1]}:{s.line})")
    return "\n".join(lines)


def _render_domain(domain: dict) -> str:
    out: list[str] = []
    ans = domain.get("ansible") or {}
    if ans.get("roles"):
        out.append(f"Ansible: {len(ans['roles'])} ролей, {len(ans.get('playbooks', []))} плейбуков, "
                   f"{len(ans.get('role_edges', []))} межролевых зависимостей")
    tf = domain.get("terraform") or {}
    if tf.get("resources") or tf.get("modules"):
        out.append(f"Terraform: {len(tf.get('modules', []))} модулей, {len(tf.get('resources', []))} ресурсов, "
                   f"{len(tf.get('variables', []))} переменных, провайдеры={tf.get('providers')}")
    vg = domain.get("vagrant") or []
    if vg:
        total_prov = sum(len(v.get("provision_chain", [])) for v in vg)
        debt = sum(v.get("commented_provisions", 0) for v in vg)
        out.append(f"Vagrant: {len(vg)} Vagrantfile, {total_prov} provision-шагов, "
                   f"техдолг(закомм. provision)={debt}")
    return "\n".join(out)