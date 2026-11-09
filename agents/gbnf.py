"""Генератор GBNF-грамматик для надежного JSON (Priority 1)."""
from __future__ import annotations

def get_unified_grammar() -> str:
    """
    Грамматика позволяет модели либо выдать JSON-вызов инструмента,
    либо обычный текст (финальный ответ). Это защищает от зацикливания.
    """
    return r'''
root ::= json_call | text

json_call ::= "{" ws "\"name\"" ws ":" ws string ws "," ws "\"arguments\"" ws ":" ws object ws "}"
object ::= "{" ws ( pair ( ws "," ws pair )* )? ws "}"
pair ::= string ws ":" ws value
value ::= string | number | "true" | "false" | "null" | object | array
array ::= "[" ws ( value ( ws "," ws value )* )? ws "]"

text ::= [^\x00-\x1F"{}]+ ( "\"" [^\x00-\x1F"{}]* )?
# Разрешаем кириллицу, латиницу, знаки препинания и переносы строк (кроме незакрытых скобок)
string ::= "\"" [a-zA-Z0-9_\-а-яА-ЯёЁ \.]+ "\""
number ::= "-"? [0-9]+ ( "." [0-9]+ )?
ws ::= [ \t\n]*
'''