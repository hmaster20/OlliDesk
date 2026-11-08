"""Генератор GBNF-грамматик для надежного JSON (Priority 1)."""
from __future__ import annotations
import json

def json_schema_to_gbnf(schema: dict) -> str:
    """Упрощенный конвертер JSON Schema в GBNF грамматику llama.cpp."""
    # Для инструментов мы ожидаем строго объект: {"name": "...", "arguments": {...}}
    # Это базовая грамматика, которая не даст модели выдать невалидный JSON.
    return r'''
root ::= "{" ws "\"name\"" ws ":" ws string ws "," ws "\"arguments\"" ws ":" ws object ws "}"
string ::= "\"" [a-zA-Z0-9_\-]+ "\""
object ::= "{" ws ( pair ( ws "," ws pair )* )? ws "}"
pair ::= string ws ":" ws value
value ::= string | number | "true" | "false" | "null" | object | array
array ::= "[" ws ( value ( ws "," ws value )* )? ws "]"
number ::= "-"? [0-9]+ ( "." [0-9]+ )?
ws ::= [ \t\n]*
'''

def get_tool_call_grammar() -> str:
    """Возвращает GBNF для вызова инструментов."""
    return json_schema_to_gbnf({})