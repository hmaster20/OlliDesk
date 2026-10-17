"""Hygiene + preview для web_search. Самодостаточен, не зависит от невидимых тел.

Регулярки redaction собраны под РЕАЛЬНЫЕ домены ваших проектов.
"""
from __future__ import annotations

import re
from contextvars import ContextVar

from core.security import NetworkMode, SecurityPolicy, redact_query

# Per-request override от глобуса (ставит ui/chat/panel перед запуском запроса).
ALLOW_NETWORK_ONCE: ContextVar[bool] = ContextVar("allow_network_once", default=False)

_TECH_ALLOWLIST = re.compile(
    r"\b(?:python|pyside6?|django|fastapi|flask|ansible|terraform|opentofu|vagrant|"
    r"virtualbox|vmware|docker|nexus|c#|csharp|dotnet|\.net|msbuild|roslyn|winforms|wpf|"
    r"asp\.net|mstest|xunit|ruby|rails|rake|bundler|solargraph|hcl|yaml|json|toml|"
    r"sqlite|chromadb|faiss|llama\.cpp|ollama|qwen|mistral|tree-?sitter|pygls|lsp|"
    r"msb\d{4}|nuget|pip|gem|apt|systemd|sshd|multipathd|unattended-upgrades|"
    r"smart-?lab|moex|акра|эксперт\s*ра|доход|cbr|ключевая\s*ставка|инфляция|"
    r"rvi|imoex|ofz|oblg|etf|isd|исин|secid|ytm|ltm|ebitda|ips|иис)\b", re.I)
_ERRCODE = re.compile(
    r"\b(?:error|exception|failed|warning|traceback|0x[0-9a-f]+|exit\s*code|tls|ssl|certificate)\b", re.I)
_PATH_RESIDUAL = re.compile(r"[/\\]\w+[/\\]\w+")
_CAMEL = re.compile(r"\b[A-Z][a-z]+[A-Z]")   # имена классов проекта


def clean_query(query: str, policy: SecurityPolicy) -> tuple[str, list[str], bool]:
    """-> (clean, redacted_categories, neutral_ok). neutral_ok=False => в neutral слать нельзя."""
    if not policy.redaction_enabled:
        return query, [], True
    cleaned, hits = redact_query(query)
    has_tech = bool(_TECH_ALLOWLIST.search(cleaned) or _ERRCODE.search(cleaned))
    residual = bool(_PATH_RESIDUAL.search(cleaned)) or bool(_CAMEL.search(cleaned))
    neutral_ok = has_tech and not residual and bool(cleaned.strip())
    return cleaned.strip(), hits, neutral_ok


def preview_web_search(query: str, policy: SecurityPolicy) -> dict:
    """Для UI-диалога: что РЕАЛЬНО уйдёт в сеть. Диалог показывает ТОЛЬКО это."""
    mode = policy.effective_web_mode()
    if mode is NetworkMode.OFF:
        return {"allowed": False, "reason": "off/confidential", "clean": "", "categories": []}
    clean, hits, neutral_ok = clean_query(query, policy)
    if mode is NetworkMode.NEUTRAL_ONLY and not neutral_ok:
        return {"allowed": False, "reason": "non_neutral", "clean": clean, "categories": hits}
    return {"allowed": True, "reason": "", "clean": clean, "categories": hits}