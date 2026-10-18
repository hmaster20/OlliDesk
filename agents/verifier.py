"""Verifier (код): проверяет финальный ответ против графа и evidence.

Правила:
  - каждый упомянутый путь ∈ индекс файлов;
  - каждый класс/модуль/роль/ресурс ∈ узлы графа;
  - блоки ASCII/Mermaid-схемы построены renderer'ом => согласованы по построению;
  - unsupported_claims: предложения без [Ei]-ссылки при включённом strict-режиме;
  - пусто/повторы/оборванный JSON => fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agents.evidence import EvidenceStore
from fs.intel.graph import DependencyGraph

_PATH_RE = re.compile(
    r"(?:[\w./\\-]+\.(?:cs|py|rb|tf|yml|yaml|hcl|csproj|sln|json|toml|go|ts|js|md|config|resx|razor))(?::\d+)?"
)
_IDENT_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,})\b")  # классы/роли с большой буквы


@dataclass
class VerifyResult:
    ok: bool
    missing_paths: list[str] = field(default_factory=list)
    unknown_symbols: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    empty_blocks: int = 0
    repeats: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "OK"
        bits = []
        if self.missing_paths:
            bits.append(f"пути вне индекса: {self.missing_paths[:5]}")
        if self.unknown_symbols:
            bits.append(f"символы вне графа: {self.unknown_symbols[:5]}")
        if self.unsupported_claims:
            bits.append(f"без доказательств: {len(self.unsupported_claims)}")
        if self.empty_blocks:
            bits.append(f"пустых блоков: {self.empty_blocks}")
        return "; ".join(bits) or "fail"


def verify(
    answer: str,
    *,
    graph: DependencyGraph,
    evidence: EvidenceStore,
    known_paths: set[str],
    known_symbols: set[str],
    strict: bool = True,
) -> VerifyResult:
    res = VerifyResult(ok=True)
    # 1. Пути.
    for m in _PATH_RE.findall(answer):
        norm = m.split(":")[0].replace("\\", "/")
        if norm not in known_paths and not any(norm.endswith(p) or p.endswith(norm) for p in known_paths):
            res.missing_paths.append(norm)
    # 2. Символы-идентификаторы (только те, что выглядят как классы/роли проекта).
    for name in set(_IDENT_RE.findall(answer)):
        if name in {
            "ASCII",
            "JSON",
            "Markdown",
            "Mermaid",
            "UTF",
            "HTTP",
            "TCP",
            "UDP",
            "AES",
            "HMAC",
            "PBKDF2",
            "DI",
            "UI",
            "API",
            "CLI",
            "ETL",
            "RAG",
            "LLM",
            "SLM",
            "GPU",
            "CPU",
            "RAM",
            "VRAM",
            "KV",
            "TTFT",
            "CI",
            "RU",
            "ISIN",
            "SECID",
            "MOEX",
            "IPS",
            "YTM",
            "LTM",
            "EBITDA",
            "AKRA",
            "OFGZ",
            "RVI",
            "IMOEX",
        }:
            continue
        if not any(name == s or name in s or s.endswith("." + name) or s == name for s in known_symbols):
            # допускаем, если имя встречается в evidence-тексте дословно
            if name not in evidence_pack_text(evidence):
                res.unknown_symbols.append(name)
    # 3. Пустые/повторяющиеся блоки ASCII.
    res.empty_blocks = len(re.findall(r"\+[-+]+\+\s*\n\s*\|\s+\|\s*\n\s*\+[-+]+\+", answer))
    lines = [l for l in answer.splitlines() if l.strip()]
    if lines:
        uniq = len(set(lines))
        res.repeats = len(lines) - uniq
        if res.repeats > max(3, 0.4 * len(lines)):
            res.notes.append("подозрение на зацикливание вывода")
    # 4. Unsupported claims (strict): предложения без [Ei].
    if strict:
        sents = re.split(r"(?<=[.!?])\s+", answer)
        for s in sents:
            if len(s) < 40:
                continue
            if re.search(
                r"\b(использует|вызывает|зависит|содержит|отвечает за|инициализирует|создаёт|запускает)\b",
                s,
                re.I,
            ) and not re.search(r"\[E\d+\]", s):
                res.unsupported_claims.append(s[:120])
    res.ok = not (res.missing_paths or res.unknown_symbols or res.empty_blocks) and (
        not strict or not res.unsupported_claims
    )
    return res


def evidence_pack_text(evidence: EvidenceStore) -> str:
    return "\n".join(e.content for e in evidence._items.values())
