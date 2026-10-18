"""DependencyGraph: узлы (файл/модуль/класс/роль/ресурс/box) и рёбра
(imports|references|uses|depends_on|includes|module_call|notifies|provisions).

Хранение — SQLite (.ollidesk/graph/project_graph.sqlite3), инкрементально по
mtime/sha256 из FileRecord. Call-graph через LSP — ОПЦИОНАЛЬНОе обогащение
рёбер kind='calls'; по умолчанию не гоняем на весь проект (дорого, не нужен
для схемы компонентов).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fs.intel import FileRecord, Symbol


@dataclass
class GraphNode:
    id: str
    kind: str            # file|module|class|interface|function|method|role|task|resource|module_tf|box|provisioner|var
    name: str
    path: str | None = None
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    indegree: int = 0
    outdegree: int = 0
    score: float = 0.0


@dataclass
class GraphEdge:
    source: str
    target: str
    kind: str            # imports|references|uses|depends_on|includes|module_call|notifies|provisions|calls
    confidence: float = 1.0
    line: int | None = None


def _symbol_node_id(s: "Symbol") -> str:
    """Единая формула ID узла-символа.

    Используется и в `DependencyGraph.build` (при создании узлов), и в
    `_link_imports` (при построении рёбер). Раньше `_link_imports` пытался
    обратиться к `s.id`, но у dataclass `Symbol` нет поля `id` — падало с
    `AttributeError`. Формула вынесена в общую функцию, чтобы синхронизация
    ID между узлами и рёбрами была гарантирована по построению.
    """
    return f"{s.kind}:{s.file}::{s.qualified_name}"


class DependencyGraph:
    def __init__(self, db_path: Path | None = None) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._adj: dict[str, list[GraphEdge]] = {}
        self._radj: dict[str, list[GraphEdge]] = {}
        self._db = db_path
        self._conn: sqlite3.Connection | None = None
        if db_path:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._init_schema()

    def _init_schema(self) -> None:
        c = self._conn
        assert c is not None
        c.executescript("""
        CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, kind TEXT, name TEXT,
          path TEXT, language TEXT, metadata TEXT, score REAL);
        CREATE TABLE IF NOT EXISTS edges(source TEXT, target TEXT, kind TEXT,
          confidence REAL, line INT, PRIMARY KEY(source,target,kind));
        CREATE INDEX IF NOT EXISTS ix_edges_source ON edges(source);
        CREATE INDEX IF NOT EXISTS ix_edges_target ON edges(target);
        """)
        c.commit()

    @classmethod
    def build(cls, files: list[FileRecord], symbols: list[Symbol],
              manifests: list[str], domain: dict) -> "DependencyGraph":
        g = cls()
        # Узлы-файлы.
        for f in files:
            g.add_node(GraphNode(id=f"file:{f.path}", kind="file", name=Path(f.path).name,
                                 path=f.path, language=f.language,
                                 metadata={"is_entry": f.is_entry_candidate,
                                           "is_test": f.is_test, "is_config": f.is_config,
                                           "size": f.size, "sha": f.sha256}))
        # Узлы-символы.
        for s in symbols:
            nid = _symbol_node_id(s)
            g.add_node(GraphNode(id=nid, kind=s.kind, name=s.qualified_name, path=s.file,
                                 metadata={"signature": s.signature, **s.metadata}))
            # Ребро символ->файл (принадлежность).
            g.add_edge(GraphEdge(source=nid, target=f"file:{s.file}", kind="belongs_to"))
        # Ребра imports/references из usings/imports.
        g._link_imports(files, symbols, domain)
        # Доменные рёбра (Ansible role->role, TF module->module, Vagrant provision chain).
        g._link_domain(domain)
        g._compute_degrees()
        g._score(files, domain)
        return g

    def add_node(self, n: GraphNode) -> None:
        self.nodes[n.id] = n

    def add_edge(self, e: GraphEdge) -> None:
        self.edges.append(e)
        self._adj.setdefault(e.source, []).append(e)
        self._radj.setdefault(e.target, []).append(e)

    def _link_imports(self, files, symbols, domain) -> None:
        path_to_file_node = {f.path: f"file:{f.path}" for f in files}
        # C#: usings -> namespace-узлы; Python: imports -> module-узлы.
        ns_index: dict[str, list[str]] = {}
        for s in symbols:
            if s.kind in {"class", "interface", "struct", "enum", "module", "namespace"}:
                ns = s.metadata.get("namespace")
                if ns:
                    # ИСПРАВЛЕНИЕ: используем общую функцию _symbol_node_id,
                    # чтобы ID совпадал с узлом, созданным в build().
                    ns_index.setdefault(ns, []).append(_symbol_node_id(s))
        for s in symbols:
            for u in s.metadata.get("usings", []) or []:
                # совпадение по префиксу неймспейса
                for ns, ids in ns_index.items():
                    if u == ns or u.startswith(ns + ".") or ns.startswith(u + "."):
                        for tid in ids:
                            # ИСПРАВЛЕНИЕ: тоже через _symbol_node_id вместо s.id.
                            self.add_edge(GraphEdge(
                                source=_symbol_node_id(s),
                                target=tid,
                                kind="references",
                                confidence=0.7,
                            ))
        # Python import -> file-узел по модульному имени.
        mod_index = {f.module_name: path_to_file_node.get(f.path) for f in files if f.module_name}
        for s in symbols:
            for imp in s.metadata.get("imports_head", []) or []:
                head = imp.split(".")[0]
                tgt = mod_index.get(head)
                if tgt and tgt != path_to_file_node.get(s.file):
                    self.add_edge(GraphEdge(source=f"file:{s.file}", target=tgt, kind="imports", confidence=0.8))

    def _link_domain(self, domain: dict) -> None:
        ans = domain.get("ansible") or {}
        for e in ans.get("role_edges", []):
            self.add_edge(GraphEdge(source=f"role:{e['from']}", target=f"role:{e['to']}",
                                    kind="depends_on", confidence=0.9))
            self.add_node(GraphNode(id=f"role:{e['from']}", kind="role", name=e["from"]))
            self.add_node(GraphNode(id=f"role:{e['to']}", kind="role", name=e["to"]))
        for r in ans.get("roles", []):
            self.add_node(GraphNode(id=f"role:{r['name']}", kind="role", name=r["name"], path=r["path"],
                                    metadata={"tasks": len(r.get("tasks", [])), "handlers": len(r.get("handlers", []))}))
            for t in r.get("tasks", []):
                self.add_edge(GraphEdge(source=f"role:{r['name']}", target=f"file:{t}", kind="includes"))
        tf = domain.get("terraform") or {}
        for m in tf.get("modules", []):
            self.add_node(GraphNode(id=m, kind="module_tf", name=m))
        for r in tf.get("resources", []):
            self.add_node(GraphNode(id=r, kind="resource", name=r))
        vg = domain.get("vagrant") or []
        for v in vg:
            vid = f"vagrant:{v['path']}"
            self.add_node(GraphNode(id=vid, kind="box", name=Path(v["path"]).name, path=v["path"],
                                    metadata={"boxes": v.get("boxes"), "providers": v.get("providers"),
                                              "env_vars": v.get("env_vars"),
                                              "commented_provisions": v.get("commented_provisions")}))
            for i, pr in enumerate(v.get("provision_chain", [])):
                pid = f"{vid}#prov{i}"
                self.add_node(GraphNode(id=pid, kind="provisioner", name=pr.get("target", f"step{i}")[:60],
                                        metadata=pr))
                self.add_edge(GraphEdge(source=vid, target=pid, kind="provisions", line=i))

    def _compute_degrees(self) -> None:
        for e in self.edges:
            if e.source in self.nodes and e.target in self.nodes and e.kind != "belongs_to":
                self.nodes[e.source].outdegree += 1
                self.nodes[e.target].indegree += 1

    def _score(self, files, domain) -> None:
        import math
        mentioned = self._doc_mentions(domain)
        for n in self.nodes.values():
            s = 0.0
            if n.metadata.get("is_entry"):
                s += 10.0
            if n.kind in {"class", "role", "module_tf", "resource", "box"}:
                s += 2.0 * math.log1p(n.indegree) + 1.0 * math.log1p(n.outdegree)
            if n.name in mentioned or (n.path and any(m in n.path for m in mentioned)):
                s += 3.0
            if n.kind in {"var", "method", "function", "task", "provisioner"}:
                s -= 1.0     # листы вниз по приоритету для L1-отбора
            if n.metadata.get("is_test"):
                s -= 2.0
            n.score = round(s, 3)

    def _doc_mentions(self, domain) -> set[str]:
        out: set[str] = set()
        # из contract_md (AGENTS.md/PLAN.md) roadmap/ветки/риски
        for piece in (domain.get("ansible_partial") or []) + (domain.get("terraform_partial") or []) + (domain.get("vagrant_partial") or []):
            pass
        return out   # заполняется в repomap из файлов-контрактов

    def top_nodes(self, kinds: Iterable[str], limit: int) -> list[GraphNode]:
        ks = set(kinds)
        cand = [n for n in self.nodes.values() if n.kind in ks or n.metadata.get("is_entry")]
        cand.sort(key=lambda n: -n.score)
        return cand[:limit]

    def resolve_root(self, root: str) -> str:
        """Приводит голый путь entry-point к ID узла графа (`file:<путь>`).

        `ProjectIntel.entry_points` — это относительные пути файлов, а ID узлов
        в графе — `file:<путь>`; без нормализации BFS от корней ничего не находит.
        """
        if root in self.nodes:
            return root
        prefixed = f"file:{root}"
        if prefixed in self.nodes:
            return prefixed
        return root

    def subgraph(self, roots: list[str], depth: int = 2) -> list[GraphEdge]:
        """BFS-подграф от корней — для evidence-пакета и renderer'а."""
        norm = [self.resolve_root(r) for r in roots]
        seen = set(norm)
        frontier = list(norm)
        out: list[GraphEdge] = []
        for _ in range(depth):
            nxt: list[str] = []
            for nid in frontier:
                for e in self._adj.get(nid, []) + self._radj.get(nid, []):
                    out.append(e)
                    other = e.target if e.source == nid else e.source
                    if other not in seen:
                        seen.add(other); nxt.append(other)
            frontier = nxt
            if not frontier:
                break
        # дедуп
        uniq: dict[tuple, GraphEdge] = {}
        for e in out:
            uniq[(e.source, e.target, e.kind)] = e
        return list(uniq.values())

    def persist(self) -> None:
        if not self._conn:
            return
        import json
        c = self._conn
        c.executemany("INSERT OR REPLACE INTO nodes VALUES(?,?,?,?,?,?,?)",
                      [(n.id, n.kind, n.name, n.path, n.language, json.dumps(n.metadata, ensure_ascii=False), n.score)
                       for n in self.nodes.values()])
        c.executemany("INSERT OR REPLACE INTO edges VALUES(?,?,?,?,?)",
                      [(e.source, e.target, e.kind, e.confidence, e.line) for e in self.edges])
        c.commit()