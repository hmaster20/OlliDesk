"""Регрессионные тесты render_architecture.

Ловят две реальные аварии:
1. Корни-пути entry_points (`LocalMessenger/Program.cs`) не совпадали с
   ID узлов графа (`file:LocalMessenger/Program.cs`) -> пустой подграф.
2. Пустой подgraf падал с AttributeError: 'list' object has no attribute 'name'
   из-за двойной вложенности `[_topo_flat(...)]`.
"""

from __future__ import annotations

from agents.diagram import render_architecture
from fs.intel.graph import DependencyGraph, GraphEdge, GraphNode


def _graph() -> DependencyGraph:
    g = DependencyGraph()
    g.add_node(
        GraphNode(
            id="file:app/main.py",
            kind="file",
            name="main.py",
            path="app/main.py",
            metadata={"is_entry": True},
            score=10.0,
        )
    )
    g.add_node(
        GraphNode(
            id="class:app/main.py::App",
            kind="class",
            name="App",
            path="app/main.py",
            metadata={"namespace": "app"},
            score=5.0,
        )
    )
    g.add_node(
        GraphNode(
            id="class:app/util.py::Helper",
            kind="class",
            name="Helper",
            path="app/util.py",
            metadata={"namespace": "app"},
            score=3.0,
        )
    )
    g.add_node(GraphNode(id="file:app/util.py", kind="file", name="util.py", path="app/util.py", score=2.0))
    g.add_edge(GraphEdge(source="class:app/main.py::App", target="file:app/main.py", kind="belongs_to"))
    g.add_edge(
        GraphEdge(
            source="class:app/main.py::App",
            target="class:app/util.py::Helper",
            kind="references",
            confidence=0.7,
        )
    )
    g.add_edge(GraphEdge(source="class:app/util.py::Helper", target="file:app/util.py", kind="belongs_to"))
    return g


class TestRenderArchitecture:
    def test_bare_path_roots(self):
        d = render_architecture(_graph(), ["app/main.py"])
        assert d.ascii
        assert "main.py" in d.ascii
        assert "file:app/main.py" in d.nodes_used

    def test_prefixed_roots(self):
        d = render_architecture(_graph(), ["file:app/main.py"])
        assert "main.py" in d.ascii

    def test_empty_graph_no_crash(self):
        d = render_architecture(DependencyGraph(), ["missing/path.py"])
        assert d.ascii == ""
        assert d.nodes_used == []

    def test_unknown_root_isolated_entry(self):
        g = _graph()
        g.add_node(
            GraphNode(
                id="file:proj/proj.csproj",
                kind="file",
                name="proj.csproj",
                path="proj/proj.csproj",
                metadata={"is_entry": True},
                score=10.0,
            )
        )
        d = render_architecture(g, ["proj/proj.csproj"])
        assert "proj.csproj" in d.ascii

    def test_edges_exclude_belongs_to(self):
        d = render_architecture(_graph(), ["app/main.py"])
        assert all(kind != "belongs_to" for _, _, kind in d.edges_used)
