"""Project Intelligence Layer: детерминированное понимание проекта.

LLM НЕ читает проект «в лоб». Этот слой строит карту, граф и доказательства;
модель лишь объясняет и дополняет знаниями. Паритет с OpenCode: AST/Repo-Map
+ доменные анализаторы + (опц.) LSP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fs.intel.graph import DependencyGraph, GraphEdge, GraphNode
from fs.intel.repomap import RepoMap, build_repomap


@dataclass
class FileRecord:
    path: str
    language: str
    size: int
    mtime: float
    sha256: str
    is_source: bool
    is_config: bool
    is_doc: bool
    is_test: bool
    is_entry_candidate: bool
    module_name: str | None = None
    namespace: str | None = None
    package: str | None = None


@dataclass
class Symbol:
    qualified_name: str
    kind: str  # class|interface|struct|enum|function|method|role|task|resource|module|box|provisioner
    file: str
    line: int
    signature: str
    parent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectIntel:
    root: str
    file_count: int
    total_bytes: int
    languages: dict[str, int]
    manifests: list[str]
    entry_points: list[str]
    symbols: list[Symbol]
    graph: DependencyGraph
    domain: dict[str, Any]  # AnsibleProject/TerraformProject/VagrantProject/...
    confidence: float
    repomap: RepoMap


def analyze_project(
    root: Path, *, indexer=None, cache_dir: Path | None = None, enable_lsp: bool = False
) -> ProjectIntel:
    """Единая точка входа. Инкрементально: повторный вызов по mtime/sha256
    переиспользует граф и резюме (см. EvidenceStore.get_module_summary)."""
    from fs.intel.langs import detect_and_parse_all

    files, symbols, manifests, entries, domain = detect_and_parse_all(root, indexer=indexer)
    graph = DependencyGraph.build(files, symbols, manifests, domain)
    repomap = build_repomap(files, symbols, entries, manifests, domain)
    langs: dict[str, int] = {}
    for f in files:
        langs[f.language] = langs.get(f.language, 0) + 1
    conf = _confidence(files, symbols, entries, manifests, domain)
    return ProjectIntel(
        root=str(root),
        file_count=len(files),
        total_bytes=sum(f.size for f in files),
        languages=langs,
        manifests=manifests,
        entry_points=entries,
        symbols=symbols,
        graph=graph,
        domain=domain,
        confidence=conf,
        repomap=repomap,
    )


def _confidence(files, symbols, entries, manifests, domain) -> float:
    """Эвристика уверенности: есть entry + манифест + символы + домен => высоко."""
    score = 0.0
    score += 0.3 if entries else 0.0
    score += 0.2 if manifests else 0.0
    score += 0.3 * min(1.0, len(symbols) / max(1, len(files) * 2))
    score += 0.2 if domain else 0.0
    return round(score, 3)
