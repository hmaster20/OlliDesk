"""Локальность и защита от утечек — проверяемые инварианты OlliDesk.

Локальность критична: облачные API отсутствуют архитектурно (нет клиента).
Единственный исходящий трафик — скачивание моделей (HuggingFace) и,
опционально, dev-инструментов (LSP-серверы). web_search остаётся как
инструмент обогащения, но проходит через hygiene-фильтр (см.
tools/web_search_hygiene.py) и allowlist ниже.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

from loguru import logger


class NetworkMode(str, Enum):
    """Режим исходящего трафика для web_search."""
    OFF = "off"                # полный запрет (confidential-проекты)
    NEUTRAL_ONLY = "neutral"   # только нейтральные технологии (дефолт)
    ASK_FIRST = "ask_first"    # диалог перед каждым запросом + hygiene
    AUTO = "auto"              # hygiene обязателен, диалог не спрашиваем


# Единственные хосты, которым разрешён исходящий трафик. Всё остальное —
# ошибка конфигурации (проверяется тестом-гардом в CI, см. §9).
OUTBOUND_ALLOWLIST: Final[frozenset[str]] = frozenset({
    "huggingface.co",
    "hf-mirror.com",
    "cas-bridge.xethub.hf.co",     # CDN HF при скачивании gguf
    "nuget.org", "api.nuget.org",  # LSP: omnisharp/roslyn (dev-инструмент)
    "pypi.org", "files.pythonhosted.org",  # LSP: pylsp и т.п.
    "rubygems.org",                # LSP: solargraph
    "yandex.ru", "duckduckgo.com", "html.duckduckgo.com",  # web_search
    "searx.be", "search.bus-hit.me", "priv.au",            # web_search fallback
})


@dataclass
class SecurityPolicy:
    """Политика безопасности проекта/глобальная."""
    mode: NetworkMode = NetworkMode.NEUTRAL_ONLY
    telemetry: bool = False                 # всегда False; поле для явного аудита
    confidential: bool = False              # True => mode принудительно OFF
    allow_lsp_download: bool = True         # dev-инструменты из allowlist
    lsp_download_size_mb_warn: int = 50     # порог «большой объём» → спросить/фон
    redaction_enabled: bool = True

    def effective_web_mode(self) -> NetworkMode:
        if self.confidential:
            return NetworkMode.OFF
        return self.mode

    @staticmethod
    def from_dict(d: dict) -> "SecurityPolicy":
        net = d.get("network", {}) if isinstance(d, dict) else {}
        mode_raw = str(net.get("web_search", "neutral")).lower()
        try:
            mode = NetworkMode(mode_raw)
        except ValueError:
            mode = NetworkMode.NEUTRAL_ONLY
        return SecurityPolicy(
            mode=mode,
            telemetry=bool(d.get("telemetry", False)),
            confidential=bool(d.get("confidential", False)),
            allow_lsp_download=bool(net.get("lsp_download", True)),
            lsp_download_size_mb_warn=int(net.get("lsp_download_size_mb_warn", 50)),
        )


def assert_outbound_allowed(url: str) -> None:
    """Гард: любой исходящий HTTP обязан попасть в allowlist.

    Вызывается в web_search / HF-download / LSP-installer перед сетевым
    обращением. Нарушение — не «warning», а исключение: локальность не
    должна зависеть от доброй воли вызывающего кода.
    """
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if not any(host == a or host.endswith("." + a) for a in OUTBOUND_ALLOWLIST):
        raise PermissionError(
            f"Outbound blocked: {host!r} not in allowlist. "
            f"Локальность критична — добавьте хост в OUTBOUND_ALLOWLIST "
            f"осознанно и с ревью."
        )


# --- Регулярки для hygiene-фильтра web_search (см. tools/web_search_hygiene.py) ---
# Собраны под РЕАЛЬНЫЕ домены ваших проектов: номера договоров Сбера
# (machine_138), ISIN/SECID, пути/неймспейсы (LocalMessenger), env-Vagrant.
_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    ("contract_number", re.compile(r"\b\d{4,5}-\d{4,6}(?:-\d)?\b")),      # договор Сбера
    ("sber_account",    re.compile(r"\b(?:счёт|счет|лицевой)\b[^,.\n]{0,40}", re.I)),
    ("inn",             re.compile(r"\b\d{10}(\d{2})?\b")),
    ("snils",           re.compile(r"\b\d{3}-\d{3}-\d{3}\s*\d{2}\b")),
    ("abs_path",        re.compile(r"(?:[A-Za-z]:\\|/(?:home|Users|mnt)/)[^\s\"']+")),
    ("rel_path_src",    re.compile(r"\b[\w./\\-]+\.(?:cs|py|rb|tf|yml|yaml|hcl|csproj|sln|json|toml)\b")),
    ("dotnet_ns",       re.compile(r"\b(?:LocalMessenger|Hmaster20)(?:\.[\w.]+)+\b")),
    ("env_secret",      re.compile(r"\b(?:PROXY|DNS_IP|DNS_NAME|TOKEN|SECRET|PASSWORD|API[_-]?KEY)\b\s*[:=]", re.I)),
    ("host_or_ip",      re.compile(r"\b(?:https?://|ssh://)?[\w.-]+\.(?:local|internal|corp|intra)\b|\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("vault_ref",       re.compile(r"\bvault://|ansible-vault|\$\{\s*secrets?\b", re.I)),
]


def redact_query(text: str) -> tuple[str, list[str]]:
    """Вырезать чувствительное из поискового запроса.

    Returns:
        (очищенный_текст, список сработавших категорий). Пустой список
        категорий означает «утечек не найдено, можно слать».
    """
    hits: list[str] = []
    out = text
    for name, pat in _PATTERNS:
        if pat.search(out):
            hits.append(name)
            out = pat.sub("[REDACTED]", out)
    # Схлопываем пробелы после redact.
    out = re.sub(r"\s{2,}", " ", out).strip()
    if hits:
        logger.warning(f"web_search hygiene: redacted categories={hits}")
    return out, hits


def is_neutral_query(text: str) -> bool:
    """True, если запрос состоит только из нейтральных технологий/ошибок.

    Эвристика для NEUTRAL_ONLY: нет путей, нет доменных идентификаторов,
    длина разумная, есть хотя бы один «технический» токен.
    """
    _, hits = redact_query(text)
    if hits:
        return False
    toks = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{2,}", text)
    return len(toks) >= 1 and len(text) <= 240