"""Единая точка вырезания секретов (0.1.2).

Используется в трёх местах:
  * loguru-sink в `core/logger.py` — до записи строки в stderr/файл;
  * `agents/tool_registry.py::execute` — до отправки tool-результата
    в LLM (и, следовательно, до сохранения в сессии и показа в UI);
  * `tools/web_search_hygiene.py` — до отправки запроса в интернет
    (поверх проектных паттернов из `core.security`).

Все регулярки — здесь. `core.security.redact_query` — тонкая обёртка
для обратной совместимости: сначала вызывает `redact()`, затем
применяет свои проектные паттерны (номера договоров, пути, IP, …).
"""

from __future__ import annotations

import re
from typing import Final

# (category, pattern, replacement)
# `replacement` — строка с группами (`\1`, `\2`) или литерал.
_PATTERNS: Final[tuple[tuple[str, re.Pattern[str], str], ...]] = (
    # --- API-ключи ---
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED:api_key]"),
    ("hf_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "[REDACTED:hf_token]"),
    ("gh_token", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "[REDACTED:gh_token]"),
    ("gh_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED:gh_token]"),
    ("slack_token", re.compile(r"\bxox[bpors]-[A-Za-z0-9-]{10,}\b"), "[REDACTED:slack_token]"),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws_access_key]"),
    # --- JWT ---
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "[REDACTED:jwt]",
    ),
    # --- Private keys (PEM) ---
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED:private_key]",
    ),
    # --- Пароль в URL / DSN: scheme://user:password@host ---
    (
        "url_password",
        re.compile(r"(\b\w+://[^:/\s]+):([^@/\s]+)@"),
        r"\1:[REDACTED:url_password]@",
    ),
    # --- Git remote с токеном: https://token@github.com/… ---
    (
        "git_token",
        re.compile(r"(https?://)[^@/\s]+@"),
        r"\1[REDACTED:git_token]@",
    ),
    # --- Env-переменные с секретными именами.
    # Заменяем значение, ключ оставляем (для отладки: видно, ЧТО утекало).
    (
        "env_secret",
        re.compile(
            r"\b(PASSWORD|PASSWD|SECRET|SECRET_KEY|TOKEN|ACCESS_TOKEN|REFRESH_TOKEN"
            r"|API_KEY|APIKEY|PRIVATE_KEY|CLIENT_SECRET|AUTH_TOKEN)\s*[:=]\s*"
            r"['\"]?([^\s'\"#]{6,})['\"]?",
            re.IGNORECASE,
        ),
        r"\1=[REDACTED:env_secret]",
    ),
)


def redact(text: str) -> tuple[str, list[str]]:
    """Вырезает секреты из текста.

    Args:
        text: Исходный текст (лог-строка, tool-результат, аргументы).

    Returns:
        Пара ``(cleaned, hits)``: очищенный текст и список сработавших
        категорий (для аудита). Пустой список — утечек не найдено.
    """
    if not text:
        return text, []

    hits: list[str] = []
    out = text
    for name, pattern, repl in _PATTERNS:
        try:
            if pattern.search(out) is None:
                continue
        except re.error:
            continue
        hits.append(name)
        try:
            out = pattern.sub(repl, out)
        except (re.error, ValueError):
            # Битый replacement — вырезаем совпадения целиком.
            out = pattern.sub(f"[REDACTED:{name}]", out)
    return out, hits


def redact_only(text: str) -> str:
    """Возвращает только очищенный текст (без списка категорий)."""
    return redact(text)[0]


def contains_secret(text: str) -> bool:
    """True, если в тексте найдены секреты (текст не меняется)."""
    if not text:
        return False
    for _name, pattern, _repl in _PATTERNS:
        try:
            if pattern.search(text) is not None:
                return True
        except re.error:
            continue
    return False


__all__ = ["contains_secret", "redact", "redact_only"]