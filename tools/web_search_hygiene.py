"""Hygiene-фильтр web_search: обогащение без утечек.

Политика (мои рекомендации по вашему вопросу):
  • Инструмент ОСТАВЛЯЕМ: он полезен для версий ПО, RFC, doc-страниц,
    кодов ошибок (MSB4019), курсов/ставки — того, что не выведено из
    локального кода.
  • В запрос НИКОГДА не попадают: пути/неймспейсы/имена классов проекта,
    номера договоров/счетов/ИНН/СНИЛС, hostnames/IP/*.local/*.internal,
    env-секреты (PROXY/DNS_IP), vault-ссылки, содержимое файлов, git-remote.
  • Режимы (security.network.web_search в v3-конфиге):
      off        — confidential-проекты, возвращаем заглушку;
      neutral    — ДЕФОЛТ: шлём только технологии/ошибки, остальное redact/отклоняем;
      ask_first  — UI-диалог + hygiene (как в текущем ToolPolicy.ASK_FIRST);
      auto       — hygiene обязателен, диалог не спрашиваем.
  • Регулярки redact — в `core/security.py:redact_query` (уже есть).
"""

from __future__ import annotations

import re

from core.security import NetworkMode, SecurityPolicy, redact_query

# Технологии/фреймворки/ошибки, которые МОГУТ оставаться в запросе.
# Важно: это allowlist (что разрешено), а не blocklist — лишние термы
# безопасны, отсутствие нужного терма — единственный риск (тогда
# neutral-режим отклонит запрос).
_TECH_ALLOWLIST = re.compile(
    r"\b(?:python|pyside6?|django|fastapi|flask|ansible|terraform|opentofu|vagrant|"
    r"virtualbox|vmware|docker|nexus|c#|csharp|dotnet|\.net|msbuild|roslyn|winforms|wpf|"
    r"asp\.net|mstest|xunit|ruby|rails|rake|bundler|solargraph|hcl|yaml|json|toml|"
    r"sqlite|chromadb|faiss|llama\.cpp|ollama|qwen|mistral|tree-?sitter|pygls|lsp|"
    r"msb\d{4}|nuget|pip|gem|apt|systemd|sshd|multipathd|unattended-upgrades|"
    r"smart-?lab|moex|акра|эксперт\s*ра|доход|cbr|ключевая\s*ставка|инфляция|"
    r"rvi|imoex|ofz|oblg|etf|isd|исин|secid|ytm|ltm|ebitda|ips|иис)\b",
    re.I,
)

# Ошибки/коды/CLI-флаги — тоже нейтральны.
_ERRCODE = re.compile(
    r"\b(?:error|exception|failed|warning|traceback|0x[0-9a-f]+|exit\s*code|"
    r"tls|ssl|certificate|http\s*\d{3})\b",
    re.I,
)

# Признаки остаточных путей/CamelCase-имён классов проекта.
_LEFTOVER_PATH = re.compile(r"[/\\]\w+[/\\]\w+")
_LEFTOVER_CAMEL = re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z]*\b")


def filter_query(
    query: str,
    *,
    mode: NetworkMode,
    policy: SecurityPolicy,
) -> tuple[str, list[str]]:
    """Очищает запрос для отправки в интернет.

    Args:
        query: Исходный запрос от модели.
        mode: Режим (off/neutral/ask_first/auto). ``off`` обрабатывается
            вызывающим — сюда не доходит.
        policy: Политика безопасности (для ``redaction_enabled``).

    Returns:
        ``(cleaned_query_or_empty, hits)``. Пустая строка означает «отклонить».
    """
    if not policy.redaction_enabled:
        return query, []

    cleaned, hits = redact_query(query)

    if mode is NetworkMode.NEUTRAL_ONLY:
        has_tech = bool(_TECH_ALLOWLIST.search(cleaned) or _ERRCODE.search(cleaned))
        leftover = bool(_LEFTOVER_PATH.search(cleaned)) or bool(_LEFTOVER_CAMEL.search(cleaned))
        if not has_tech or leftover:
            return "", hits + (["non_neutral_residual"] if leftover else [])

    return cleaned, hits
