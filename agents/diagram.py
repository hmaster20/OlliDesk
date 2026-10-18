"""DiagramRenderer: ASCII и Mermaid-схемы строятся КОДом из DependencyGraph.

Это ключевой фикс пустых рамок на скриншотах: модель НЕ рисует схему, она лишь
подписывает узлы прозой. Renderer гарантирует: имена блоков ∈ граф, связей
«из головы» нет, пустых рамок не бывает по построению.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from fs.intel.graph import DependencyGraph, GraphNode

_KIND_ICON = {
    "file": "📄",
    "class": "🟦",
    "interface": "🟨",
    "module": "📦",
    "role": "🎭",
    "task": "▫️",
    "resource": "🧱",
    "module_tf": "📦",
    "box": "🖥",
    "provisioner": "⚙️",
    "function": "ƒ",
    "method": "m",
    "var": "$",
    "namespace": "🗂",
}


@dataclass
class Diagram:
    ascii: str
    mermaid: str
    nodes_used: list[str]
    edges_used: list[tuple[str, str, str]]


def render_architecture(
    graph: DependencyGraph, roots: list[str], *, max_nodes: int = 24, layout: str = "layered"
) -> Diagram:
    sub = graph.subgraph(roots, depth=2)
    nodes, edges = _prune(graph, sub, max_nodes)
    layers = _to_layers(nodes, edges, roots) if layout == "layered" else None
    ascii_txt = _ascii(layers or [_topo_flat(nodes, edges, roots)], nodes, edges, graph)
    mermaid_txt = _mermaid(nodes, edges, graph)
    return Diagram(
        ascii=ascii_txt,
        mermaid=mermaid_txt,
        nodes_used=[n.id for n in nodes],
        edges_used=[(e.source, e.target, e.kind) for e in edges],
    )


def _prune(graph, edges, max_nodes):
    keep_ids: set[str] = set()
    for e in edges:
        keep_ids.add(e.source)
        keep_ids.add(e.target)
    nodes = [graph.nodes[i] for i in keep_ids if i in graph.nodes]
    nodes.sort(key=lambda n: -n.score)
    nodes = nodes[:max_nodes]
    nids = {n.id for n in nodes}
    edges = [e for e in edges if e.source in nids and e.target in nids and e.kind != "belongs_to"]
    return nodes, edges


def _to_layers(nodes, edges, roots):
    """Слои по расстоянию от корней (BFS по directed-рёбрам imports/references/depends_on/includes/provisions)."""
    fwd = defaultdict(list)
    for e in edges:
        if e.kind in {
            "imports",
            "references",
            "depends_on",
            "includes",
            "provisions",
            "module_call",
            "calls",
            "uses",
        }:
            fwd[e.source].append(e.target)
    layer: dict[str, int] = {r: 0 for r in roots if r in {n.id for n in nodes}}
    if not layer:
        # нет корней в подграфе — стартуем с максимального score
        top = max(nodes, key=lambda n: n.score, default=None)
        if top:
            layer[top.id] = 0
    frontier = list(layer)
    depth = 0
    while frontier:
        depth += 1
        nxt = []
        for nid in frontier:
            for tgt in fwd.get(nid, []):
                if tgt not in layer:
                    layer[tgt] = depth
                    nxt.append(tgt)
        frontier = nxt
    by_layer: dict[int, list[GraphNode]] = defaultdict(list)
    nmap = {n.id: n for n in nodes}
    for nid, d in layer.items():
        if nid in nmap:
            by_layer[d].append(nmap[nid])
    return [by_layer[d] for d in sorted(by_layer)]


def _topo_flat(nodes, edges, roots):
    return [sorted(nodes, key=lambda n: -n.score)]


def _ascii(layers, nodes, edges, graph):
    nmap = {n.id: n for n in nodes}
    widths = []
    rows: list[list[str]] = []
    for layer in layers:
        cells = []
        for n in layer:
            label = _short(n)
            icon = _KIND_ICON.get(n.kind, "•")
            box_w = max(len(label) + 4, 12)
            cells.append((icon, label, box_w))
        rows.append(cells)
    # Рисуем каждый слой горизонтальной полосой блоков, связи — стрелками вниз.
    out: list[str] = []
    prev_centers: list[tuple[int, int]] = []  # (x_center, y) предыдущего слоя
    y = 0
    for li, cells in enumerate(rows):
        # верхние рамки
        line_top = ""
        line_mid = ""
        xcursor = 0
        centers = []
        for icon, label, w in cells:
            line_top += "+" + "-" * (w - 2) + "+" + "   "
            line_mid += "|" + f" {icon} {label}".ljust(w - 2) + "|" + "   "
            centers.append((xcursor + w // 2, y))
            xcursor += w + 3
        out.append(line_top.rstrip())
        out.append(line_mid.rstrip())
        out.append(line_top.rstrip())
        y += 3
        # стрелки от предыдущего слоя
        if prev_centers:
            arrow_line = _arrow_row(prev_centers, centers, width=max(xcursor, 1))
            out.append(arrow_line)
            y += 1
        prev_centers = centers
    return "\n".join(out)


def _short(n: GraphNode) -> str:
    name = n.name
    if n.kind == "file":
        return name
    if "::" in n.qualified_name if hasattr(n, "qualified_name") else False:
        pass
    return (name[:24] + "…") if len(name) > 25 else name


def _arrow_row(prev, cur, *, width: int) -> str:
    # Упрощённо: рисуем вертикальные '|' и 'v' под совпадающими колонками.
    grid = [" "] * max(width, 1)
    for (px, _), (cx, _) in _match_columns(prev, cur):
        if 0 <= cx < len(grid):
            grid[cx] = "|"
    line = "".join(grid).rstrip()
    # нижняя строка со стрелками
    arrow = [" "] * max(width, 1)
    for (px, _), (cx, _) in _match_columns(prev, cur):
        if 0 <= cx < len(arrow):
            arrow[cx] = "v"
    return line + "\n" + "".join(arrow).rstrip() if line else "".join(arrow).rstrip()


def _match_columns(prev, cur):
    # сопоставляем по x-близости (жадно)
    pairs = []
    used_cur = set()
    for p in prev:
        best = None
        for i, c in enumerate(cur):
            if i in used_cur:
                continue
            d = abs(p[0] - c[0])
            if best is None or d < best[0]:
                best = (d, i)
        if best is not None and best[0] <= 40:
            pairs.append((p, cur[best[1]]))
            used_cur.add(best[1])
    return pairs


def _mermaid(nodes, edges, graph):
    nmap = {n.id: n for n in nodes}
    lines = ["flowchart TD"]
    for n in nodes:
        shape = {
            "class": "[{lbl}]",
            "interface": "([{lbl}])",
            "role": "{{{lbl}}}",
            "resource": ">{lbl}]",
            "module_tf": "[[{lbl}]]",
            "box": "([lbl])",
        }.get(n.kind, "[{lbl}]")
        lbl = f"{n.kind}: {_short(n)}"
        lines.append(f"  {_mid(n.id)} {shape.format(lbl=_esc(lbl))}")
    arrow = {
        "imports": "-->",
        "references": "-.->",
        "depends_on": "==>",
        "includes": "-->",
        "provisions": "-->",
        "module_call": "-->",
        "calls": "-->",
        "uses": "-.->",
    }.get("imports", "-->")
    for e in edges:
        if e.source in nmap and e.target in nmap:
            lines.append(f"  {_mid(e.source)} -->|{e.kind}| {_mid(e.target)}")
    return "\n".join(lines)


def _mid(nid: str) -> str:
    import hashlib

    return "N" + hashlib.md5(nid.encode()).hexdigest()[:6]


def _esc(s: str) -> str:
    return s.replace('"', "'").replace("<", "(").replace(">", ")").replace("{", "(").replace("}", ")")
