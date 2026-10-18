"""Тесты DependencyGraph: нормализация корней и BFS-подграф."""

from __future__ import annotations

from fs.intel.graph import DependencyGraph, GraphEdge, GraphNode


def _graph() -> DependencyGraph:
    g = DependencyGraph()
    g.add_node(GraphNode(id="file:a/main.py", kind="file", name="main.py", path="a/main.py"))
    g.add_node(GraphNode(id="class:a/main.py::App", kind="class", name="App", path="a/main.py"))
    g.add_edge(GraphEdge(source="class:a/main.py::App", target="file:a/main.py", kind="belongs_to"))
    return g


class TestResolveRoot:
    def test_bare_path(self):
        assert _graph().resolve_root("a/main.py") == "file:a/main.py"

    def test_already_id(self):
        assert _graph().resolve_root("file:a/main.py") == "file:a/main.py"

    def test_unknown_passthrough(self):
        assert _graph().resolve_root("ghost.py") == "ghost.py"


class TestSubgraph:
    def test_bare_path_root_finds_edges(self):
        g = _graph()
        edges = g.subgraph(["a/main.py"], depth=2)
        assert any(e.kind == "belongs_to" for e in edges)

    def test_unknown_root_empty(self):
        assert _graph().subgraph(["ghost.py"], depth=2) == []
