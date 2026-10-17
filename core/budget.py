"""Единая точка расчёта контекстного бюджета.

Заменяет конкуренцию DEFAULT_NUM_CTX / KNOWN_NUM_CTX / CHARS_PER_TOKEN /
observation_budget / compact_history / truncate_to_chars. Правило: ctx_total
берётся у ЗАГРУЖЕННОГО клиента, output_reserve — по ТИПУ операции,
tool-results живут в EvidenceStore, а не в истории.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class OpType(str, Enum):
    CLASSIFY = "classify"      # 3B: тип запроса
    PLAN = "plan"              # 3B: compact JSON-план
    TOOL_CALL = "tool_call"    # structured output
    CRITIQUE = "critique"      # 3B: проверка формата
    MODULE_SUMMARY = "mod_sum" # 7B/3B: резюме модуля (кэш)
    SYNTHESIS = "synthesis"    # 7B: финальная проза
    REGEN = "regen"            # 7B: 1 регенерация после verifier-fail


# Резервы output по операции (токены). CPU-7B дорог в длинном output —
# поэтому synthesis ограничен, а схема рисуется кодом (0 токенов).
OUTPUT_RESERVE: dict[OpType, int] = {
    OpType.CLASSIFY: 32,
    OpType.PLAN: 256,
    OpType.TOOL_CALL: 192,
    OpType.CRITIQUE: 192,
    OpType.MODULE_SUMMARY: 768,
    OpType.SYNTHESIS: 1100,
    OpType.REGEN: 900,
}

SAFETY_TOKENS = 256
# Доли заполнения доступного входа для architecture-задачи.
ARCH_SHARES = {"system": 0.12, "repo_map": 0.25, "evidence": 0.45,
               "dynamic": 0.10, "history": 0.08}


class TokenCounter(Protocol):
    """Контракт точного подсчёта (реализация — LocalLLMClient.count_tokens)."""
    def count(self, text: str) -> int: ...


class ApproxCounter:
    """Черновая оценка до загрузки модели (CHARS_PER_TOKEN fallback)."""
    CHARS_PER_TOKEN = 3.0
    def count(self, text: str) -> int:
        return max(1, int(len(text) / self.CHARS_PER_TOKEN))


@dataclass
class BudgetPlan:
    ctx_total: int
    output_reserve: int
    safety: int
    available_input: int
    slots: dict[str, int]          # system/repo_map/evidence/dynamic/history
    overflow: dict[str, int]       # сколько НЕ влезло по каждому слоту

    def fits(self) -> bool:
        return sum(self.slots.values()) <= self.available_input


def plan_budget(
    *,
    ctx_total: int,
    op: OpType,
    counter: TokenCounter,
    pieces: dict[str, str],
    shares: dict[str, float] | None = None,
) -> BudgetPlan:
    """Собрать бюджет под операцию.

    Args:
        ctx_total: runtime n_ctx загруженного клиента (НЕ словарь!).
        op: тип генерации — определяет output_reserve.
        counter: точный или приблизительный токенизатор.
        pieces: {'system':..., 'repo_map':..., 'evidence':..., 'dynamic':...,
                 'history':...} — сырой текст каждого слота.
        shares: доли; по умолчанию ARCH_SHARES.
    """
    shares = shares or ARCH_SHARES
    out_res = OUTPUT_RESERVE[op]
    avail = max(0, ctx_total - out_res - SAFETY_TOKENS)

    slots: dict[str, int] = {}
    overflow: dict[str, int] = {}
    # Считаем реальные токены каждого куска, затем режем по доле с
    # перераспределением неиспользованной квоты evidence <- repo_map <- ...
    raw = {k: counter.count(pieces.get(k, "")) for k in shares}
    quota = {k: int(avail * v) for k, v in shares.items()}
    leftover = 0
    for k in ("history", "dynamic", "repo_map", "system"):   # низший приоритет первым
        take = min(raw[k], quota[k])
        slots[k] = take
        leftover += quota[k] - take
        overflow[k] = raw[k] - take
    # evidence получает свою долю + весь leftover (главный payload арх-задачи)
    ev_quota = quota["evidence"] + leftover
    slots["evidence"] = min(raw["evidence"], ev_quota)
    overflow["evidence"] = max(0, raw["evidence"] - slots["evidence"])
    # system никогда не режем молча — это инструкция; если не влез,
    # пересобираем компактный системный промпт (см. orchestrator).
    if overflow["system"]:
        slots["system"] = raw["system"]
        slots["evidence"] = max(0, slots["evidence"] - overflow["system"])
        overflow["evidence"] += overflow["system"]
        overflow["system"] = 0

    return BudgetPlan(ctx_total, out_res, SAFETY_TOKENS, avail, slots, overflow)


def trim_to_tokens(counter: TokenCounter, text: str, limit: int) -> str:
    """Обрезать текст до limit токенов, сохраняя голову и хвост.

    Для evidence-фрагментов голова+хвост важнее середины (сигнатура класса
    в начале, closing/вызов в конце). Середина заменяется маркером.
    """
    if limit <= 0:
        return ""
    if counter.count(text) <= limit:
        return text
    # Бинарный поиск по символам под приблизительную плотность.
    approx_cpt = max(1.0, len(text) / max(1, counter.count(text)))
    target_chars = int(limit * approx_cpt)
    head = int(target_chars * 0.6)
    tail = target_chars - head
    return text[:head] + "\n…[trimmed]…\n" + text[-tail:] if tail > 0 else text[:head]