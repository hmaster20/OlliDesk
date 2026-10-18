"""Terraform/HCL анализатор по канону (module/resource/data/variable/output/
provider/locals; *.tf, *.tfvars, *.pkr.hcl). Дамп terraform-modules_2.md пуст —
реализация по стандарту hashicorp/hcl; интерфейс стабилен.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fs.intel import FileRecord, Symbol

try:
    import hcl2 as _hcl2  # python-hcl2

    _HAS_HCL2 = True
except Exception:
    _hcl2 = None
    _HAS_Hcl2 = False
    _HAS_HCL2 = False

_BLOCK_RE = re.compile(
    r'^\s*(resource|data|module|variable|output|provider|locals|terraform)\s+"?([\w.\-]+)"?\s*(?:"?([\w.\-]+)"?)?\s*\{',
    re.M,
)


def parse_hcl_file(p: Path, rec: FileRecord):
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], None
    syms: list[Symbol] = []
    blocks: list[dict] = []
    parsed: Any = None
    if _HAS_HCL2:
        try:
            parsed = _hcl2.loads(text)
        except Exception:
            parsed = None
    if isinstance(parsed, dict):
        for kind in ("resource", "data", "module", "variable", "output", "provider", "locals"):
            for blk in _iter_hcl_blocks(parsed, kind):
                addr = blk.get("__address__") or _hcl_addr(kind, blk)
                syms.append(
                    Symbol(
                        qualified_name=str(addr),
                        kind=kind,
                        file=rec.path,
                        line=1,
                        signature=f"{kind} {addr}",
                        metadata={"attrs": list(blk.keys())[:12]},
                    )
                )
                blocks.append({"kind": kind, "address": str(addr), "raw_keys": list(blk.keys())[:12]})
    else:
        for m in _BLOCK_RE.finditer(text):
            kind = m.group(1)
            a, b = m.group(2), m.group(3)
            addr = f"{kind}.{a}.{b}" if kind in {"resource", "data"} and b else (f"{kind}.{a}" if a else kind)
            line = text.count("\n", 0, m.start()) + 1
            syms.append(
                Symbol(
                    qualified_name=addr,
                    kind=kind,
                    file=rec.path,
                    line=line,
                    signature=m.group(0).strip()[:120],
                )
            )
            blocks.append({"kind": kind, "address": addr})
    # module-calls (ребро root->module) и depends_on.
    mod_calls = [b["address"] for b in blocks if b["kind"] == "module"]
    deps = re.findall(r"depends_on\s*=\s*\[([^\]]*)\]", text)
    domain = {
        "kind": "tf_file",
        "path": rec.path,
        "blocks": blocks,
        "module_calls": mod_calls,
        "depends_on_raw": deps,
        "is_vars": rec.language == "terraform-vars" or rec.path.endswith(".tfvars"),
    }
    return syms, domain


def _iter_hcl_blocks(parsed: dict, kind: str):
    node = parsed.get(kind)
    if not isinstance(node, dict):
        return
    if kind in {"resource", "data"}:
        for rtype, items in node.items():
            if isinstance(items, dict):
                for name, body in items.items():
                    if isinstance(body, dict):
                        yield {**body, "__address__": f"{kind}.{rtype}.{name}"}
    elif kind == "module":
        for name, body in node.items():
            if isinstance(body, dict):
                yield {**body, "__address__": f"module.{name}"}
    elif kind in {"variable", "output", "provider"}:
        for name, body in node.items():
            if isinstance(body, dict):
                yield {**body, "__address__": f"{kind}.{name}"}
    elif kind == "locals":
        if isinstance(node, dict):
            yield {**node, "__address__": "locals"}


def _hcl_addr(kind: str, blk: dict) -> str:
    return blk.get("name") or blk.get("type") or kind


def build_terraform_project(root: Path, files: list[FileRecord]) -> dict:
    modules: list[str] = []
    resources: list[str] = []
    variables: list[str] = []
    outputs: list[str] = []
    providers: set[str] = set()
    root_files: list[str] = []
    for f in files:
        if f.language not in {"terraform", "hcl", "terraform-vars"}:
            continue
        p = root / f.path
        _, dom = parse_hcl_file(p, f)
        if not dom:
            continue
        for b in dom["blocks"]:
            k, a = b["kind"], b["address"]
            if k == "module":
                modules.append(a)
            elif k in {"resource", "data"}:
                resources.append(a)
            elif k == "variable":
                variables.append(a)
            elif k == "output":
                outputs.append(a)
            elif k == "provider":
                providers.add(a.split(".")[-1])
        if f.path.endswith("main.tf") or f.path == "main.tf":
            root_files.append(f.path)
    return {
        "modules": modules,
        "resources": resources,
        "variables": variables,
        "outputs": outputs,
        "providers": sorted(providers),
        "root_files": root_files,
    }
