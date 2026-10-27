"""DiagramRenderer: ASCII и Mermaid-схемы строятся КОДом из DependencyGraph.

Это ключевой фикс пустых рамок на скриншотах: модель НЕ рисует схему, она лишь
подписывает узлы прозой. Renderer гарантирует: имена блоков ∈ граф, связей
«из головы» нет, пустых рамок не бывает по построению.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from fs.intel.graph import DependencyGraph, GraphEdge, GraphNode

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
    roots = [graph.resolve_root(r) for r in roots]
    sub = graph.subgraph(roots, depth=2)
    nodes, edges = _prune(graph, sub, max_nodes, roots)
    layers = _to_layers(nodes, edges, roots) if layout == "layered" else None
    ascii_txt = _ascii(layers or _topo_flat(nodes, edges, roots), nodes, edges, graph)
    mermaid_txt = _mermaid(nodes, edges, graph)
    return Diagram(
        ascii=ascii_txt,
        mermaid=mermaid_txt,
        nodes_used=[n.id for n in nodes],
        edges_used=[(e.source, e.target, e.kind) for e in edges],
    )


def _prune(
    graph: DependencyGraph,
    edges: list[GraphEdge],
    max_nodes: int,
    roots: Sequence[str] = (),
) -> tuple[list[GraphNode], list[GraphEdge]]:
    keep_ids: set[str] = set(roots)
    for e in edges:
        keep_ids.add(e.source)
        keep_ids.add(e.target)
    # Отбрасываем листья-методы/поля: они не архитектурные узлы,
    # но при `belongs_to`-BFS забивают top-N и создают «мусорные» строки
    keep_ids = {
        iid
        for iid in keep_ids
        if iid in graph.nodes
        and graph.nodes[iid].kind not in {"method", "function", "var", "task", "provisioner"}
    }
    nodes = [graph.nodes[iid] for iid in keep_ids if iid in graph.nodes]
    nodes.sort(key=lambda n: -n.score)
    nodes = nodes[:max_nodes]
    nids = {n.id for n in nodes}
    edges = [e for e in edges if e.source in nids and e.target in nids and e.kind != "belongs_to"]
    return nodes, edges


def _to_layers(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    roots: Sequence[str],
) -> list[list[GraphNode]]:
    """Слои по расстоянию от корней (BFS по directed-рёбрам imports/references/depends_on/includes/provisions)."""
    fwd: defaultdict[str, list[str]] = defaultdict(list)
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
    # Узлы, недостижимые по directed-рёбрам (символы связаны с файлами
    # только обратным belongs_to), докладываем undirected-BFS, иначе схема
    # вырождается в один слой из одних файлов.
    if len(layer) < len({n.id for n in nodes}):
        und: defaultdict[str, list[str]] = defaultdict(list)
        for e in edges:
            und[e.source].append(e.target)
            und[e.target].append(e.source)
        frontier = list(layer)
        depth = max(layer.values(), default=0)
        while frontier:
            depth += 1
            nxt = []
            for nid in frontier:
                for tgt in und.get(nid, []):
                    if tgt in {n.id for n in nodes} and tgt not in layer:
                        layer[tgt] = depth
                        nxt.append(tgt)
            frontier = nxt
        # Полностью изолированные узлы — в последний слой.
        missing = {n.id for n in nodes} - set(layer)
        if missing:
            last = max(layer.values(), default=0) + 1
            for mid in missing:
                layer[mid] = last
    by_layer: dict[int, list[GraphNode]] = defaultdict(list)
    nmap = {n.id: n for n in nodes}
    for nid, d in layer.items():
        if nid in nmap:
            by_layer[d].append(nmap[nid])
    return [by_layer[d] for d in sorted(by_layer)]


def _topo_flat(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    roots: Sequence[str],
) -> list[list[GraphNode]]:
    return [sorted(nodes, key=lambda n: -n.score)]


_MAX_ROW_WIDTH = 110


def _ascii(
    layers: list[list[GraphNode]],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    graph: DependencyGraph,
) -> str:
    if not nodes:
        return ""
    # Каждый слой пакуем в физические строки шириной <= _MAX_ROW_WIDTH,
    # иначе слой из 20+ боксов даёт строку на сотни символов.
    packed: list[list[list]] = []
    for layer in layers:
        cells = []
        for n in layer:
            label = _short(n)
            icon = _KIND_ICON.get(n.kind, "•")
            # Эмодзи занимает 2 терминальные колонки, len() считает 1 — без
            # поправки рамка уже содержимого (реальный баг на LocalMessenger).
            inner = f" {icon} {label}"
            extra = 0 if icon in {"ƒ", "m", "$", "•"} else 1
            box_w = max(len(inner) + extra + 2, 12)
            cells.append((icon, label, box_w, inner, extra))
        phys: list[list] = []
        cur: list = []
        cur_w = 0
        for c in cells:
            cw = c[2] + 3
            if cur and cur_w + cw > _MAX_ROW_WIDTH:
                phys.append(cur)
                cur = []
                cur_w = 0
            cur.append(c)
            cur_w += cw
        if cur:
            phys.append(cur)
        if phys:
            packed.append(phys)
    # Рисуем слои сверху вниз, стрелки — между слоями (перед первыми рамками).
    out: list[str] = []
    prev_centers: list[tuple[int, int]] = []
    y = 0
    for phys in packed:
        layer_centers: list[tuple[int, int]] = []
        for cells in phys:
            line_top = ""
            line_mid = ""
            xcursor = 0
            centers = []
            for _icon, _label, w, inner, extra in cells:
                line_top += "+" + "-" * (w - 2) + "+" + "   "
                line_mid += "|" + inner + " " * max(0, w - 2 - len(inner) - extra) + "|" + "   "
                centers.append((xcursor + w // 2, y))
                xcursor += w + 3
            if prev_centers and not layer_centers:
                out.append(_arrow_row(prev_centers, centers, width=max(xcursor, 1)))
                y += 1
            out.append(line_top.rstrip())
            out.append(line_mid.rstrip())
            out.append(line_top.rstrip())
            y += 3
            layer_centers.extend(centers)
        prev_centers = layer_centers
    return "\n".join(out)


def _short(n: GraphNode) -> str:
    name = n.name
    if n.kind == "file":
        return name
    if "::" in n.qualified_name if hasattr(n, "qualified_name") else False:
        pass
    return (name[:24] + "…") if len(name) > 25 else name


def _arrow_row(
    prev: list[tuple[int, int]],
    cur: list[tuple[int, int]],
    *,
    width: int,
) -> str:
    # Упрощённо: рисуем вертикальные '|' и 'v' под совпадающими колонками.
    grid = [" "] * max(width, 1)
    for (_px, _), (cx, _) in _match_columns(prev, cur):
        if 0 <= cx < len(grid):
            grid[cx] = "|"
    line = "".join(grid).rstrip()
    # нижняя строка со стрелками
    arrow = [" "] * max(width, 1)
    for (_px, _), (cx, _) in _match_columns(prev, cur):
        if 0 <= cx < len(arrow):
            arrow[cx] = "v"
    return line + "\n" + "".join(arrow).rstrip() if line else "".join(arrow).rstrip()


def _match_columns(
    prev: list[tuple[int, int]],
    cur: list[tuple[int, int]],
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
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


def _mermaid(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    graph: DependencyGraph,
) -> str:
    nmap = {n.id: n for n in nodes}
    lines = ["flowchart TD"]

    # Стиль стрелки по типу связи. Осмысленно: пунктир для «использует»,
    # жирная — для жёстких зависимостей.
    arrow_styles = {
        "imports": "-->",
        "references": "-.->",
        "depends_on": "==>",
        "includes": "-->",
        "provisions": "-->",
        "module_call": "-->",
        "calls": "-->",
        "uses": "-.->",
    }

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

    for e in edges:
        if e.source in nmap and e.target in nmap:
            style = arrow_styles.get(e.kind, "-->")
            lines.append(f"  {_mid(e.source)} {style}|{e.kind}| {_mid(e.target)}")

    return "\n".join(lines)


def _mid(nid: str) -> str:
    import hashlib

    return "N" + hashlib.md5(nid.encode()).hexdigest()[:6]


def _esc(s: str) -> str:
    return s.replace('"', "'").replace("<", "(").replace(">", ")").replace("{", "(").replace("}", ")")
