"""Надёжный tool-calling. ПОПРАВКА C1: OlliDesk использует name/arguments,
НЕ tool/args (подтверждено дампом 31: парсер состояния читает arguments.get(...),
системный промпт отдаёт примеры {"name":...,"arguments":{...}})."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Реальные имена тулов из 31 (_CHAT_AUTO_TOOLS + реестр @tool).
_TOOL_NAMES = (
    "read_file", "write_file", "list_directory", "search_codebase", "web_search",
    "get_weather", "get_current_datetime", "get_exchange_rate",
    "get_exchange_rates_multi", "get_git_status", "create_snapshot",
    "undo_last_snapshot", "read_attached_file", "record_hypothesis",
    "update_hypothesis", "list_hypotheses",
    # структурные тулы нового слоя (оркестратор/3B-планировщик):
    "get_project_map", "find_entry_points", "list_top_modules", "list_symbols",
    "get_module_summary", "find_dependencies", "read_symbol",
)

# GBNF: строго {"name":"...","arguments":{...}} ИЛИ {"final":true}.
TOOL_CALL_GBNF = r"""
root      ::= space? ( toolcall | finalcall ) space?
toolcall  ::= "{" ws "\"name\"" ws ":" ws toolname ws "," ws "\"arguments\"" ws ":" ws args "}"
finalcall ::= "{" ws "\"final\"" ws ":" ws "true" ws "}"
toolname  ::= "\"" ( %s ) "\""
args      ::= "{" ws ( pair ( ws "," ws pair )* )? ws "}"
pair      ::= string ws ":" ws value
value     ::= string | number | "true" | "false" | "null" | array | object
array     ::= "[" ws ( value ( ws "," ws value )* )? ws "]"
object    ::= args
string    ::= "\"" char* "\""
char      ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
number    ::= "-"? [0-9]+ ("." [0-9]+)?
ws        ::= [ \t\n\r]*
""" % " | ".join(f'"{n}"' for n in _TOOL_NAMES)

_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class ParsedCall:
    name: str | None          # C1: name, не tool
    arguments: dict[str, Any] # C1: arguments, не args
    is_final: bool
    raw: str
    ok: bool


def parse_tool_call(text: str) -> ParsedCall:
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    candidate = m.group(1) if m else raw
    jm = _JSON_OBJ.search(candidate)
    blob = jm.group(0) if jm else candidate
    blob = re.sub(r",\s*([}\]])", r"\1", blob)
    try:
        obj = json.loads(blob)
    except Exception:
        return ParsedCall(None, {}, False, raw, ok=False)
    if obj.get("final") is True:
        return ParsedCall(None, {}, True, raw, ok=True)
    name = obj.get("name") or obj.get("tool")          # толерантность к обоим ключам
    args = obj.get("arguments") or obj.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    if name and isinstance(name, str):
        return ParsedCall(name, args, False, raw, ok=True)
    return ParsedCall(None, {}, False, raw, ok=False)


_STRUCTURAL = {"architecture", "dependencies", "iaac_role", "terraform_module", "vagrant_box"}


def should_fallback(call: ParsedCall, task: str) -> bool:
    if task not in _STRUCTURAL:
        return False
    return (not call.ok) or (call.name is None and not call.is_final)


def looks_like_ascii_junk(text: str) -> bool:
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return False
    boxish = sum(1 for l in lines if re.match(r"^[|\-+/\\+= ]+$", l))
    uniq = len(set(lines))
    return boxish / max(1, len(lines)) > 0.6 and uniq / max(1, len(lines)) < 0.5