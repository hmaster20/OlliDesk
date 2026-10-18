"""Critic (3B, GPU): быстрая проверка СТРУКТУРЫ/ФОРМАТА ответа.

Не оценивает истинность (это verifier-код), а ловит: отсутствие секций,
оборванный markdown, JSON-мусор в прозе, слишком длинный/короткий ответ.
Дешёвый вызов (<2 с), температура 0.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "missing_sections": {"type": "array", "items": {"type": "string"}},
        "format_issues": {"type": "array", "items": {"type": "string"}},
        "hint": {"type": "string"},
    },
    "required": ["ok", "missing_sections", "format_issues", "hint"],
}

REQUIRED_SECTIONS = {
    "architecture": ["обзор", "компоненты", "схема", "поток"],
    "dependencies": ["зависимости"],
    "iaac_role": ["роль", "задачи"],
    "terraform_module": ["модуль", "ресурсы"],
}


@dataclass
class Critique:
    ok: bool
    missing_sections: list[str]
    format_issues: list[str]
    hint: str


async def critique(answer: str, task: str, planner_client) -> Critique:
    req = REQUIRED_SECTIONS.get(task, [])
    low = answer.lower()
    missing = [s for s in req if s not in low]
    issues: list[str] = []
    if answer.count("```") % 2:
        issues.append("незакрытый код-блок")
    if re.search(r"\{\s*\"?(tool|name|arguments)\"?\s*:", answer):
        issues.append("JSON tool-объект в теле ответа")
    if len(answer) < 200:
        issues.append("слишком короткий ответ")
    # Дёшево: если код-проверки чистые и секции на месте — не дёргаем 3B.
    if not missing and not issues:
        return Critique(True, [], [], "")
    prompt = (
        "Проверь структуру ответа архитектора. Верни ТОЛЬКО JSON по схеме.\n"
        f"Схема: {json.dumps(CRITIC_SCHEMA, ensure_ascii=False)}\n"
        f"Требуемые секции: {missing or req}\n"
        f"Замеченные проблемы: {issues}\n"
        "--- ОТВЕТ ---\n" + answer[:6000]
    )
    try:
        raw = await planner_client.generate(
            prompt, max_tokens=192, temperature=0.0, json_schema=CRITIC_SCHEMA
        )
        obj = json.loads(raw if isinstance(raw, str) else raw.content)
        return Critique(
            bool(obj.get("ok", not missing and not issues)),
            obj.get("missing_sections", missing),
            obj.get("format_issues", issues),
            obj.get("hint", ""),
        )
    except Exception:
        return Critique(not missing and not issues, missing, issues, "критик недоступен")
