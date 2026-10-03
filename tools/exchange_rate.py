"""Курсы валют через open.er-api.com (бесплатный, без API-ключа)."""

import time

import httpx
from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy

# Кэш курсов (на 30 минут)
_RATES_CACHE: dict[str, tuple[float, dict[str, float]]] = {}

_RATES_URL = "https://open.er-api.com/v6/latest/{base}"

_HTTP_HEADERS = {
    "User-Agent": "OlliDesk/0.1",
    "Accept": "application/json",
}


async def _fetch_rates(base: str) -> dict[str, float]:
    """Возвращает словарь {ISO: rate} для базы base."""
    cache_key = base.upper()
    if cache_key in _RATES_CACHE:
        ts, rates = _RATES_CACHE[cache_key]
        if time.time() - ts < 1800:
            return rates

    url = _RATES_URL.format(base=cache_key)
    async with httpx.AsyncClient(headers=_HTTP_HEADERS, timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    if data.get("result") != "success":
        raise RuntimeError(data.get("error-type", "unknown error"))

    rates = data.get("rates") or {}
    if not rates:
        raise RuntimeError("empty rates")

    _RATES_CACHE[cache_key] = (time.time(), rates)
    return rates


@tool(
    name="get_exchange_rate",
    description=(
        "Возвращает АКТУАЛЬНЫЙ курс валюты на сегодня. "
        "Используй при вопросах «какой курс доллара/евро/юаня к рублю», "
        "«сколько стоит доллар», «курс валют» и т.п."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
async def get_exchange_rate(base: str = "USD", target: str = "RUB") -> str:
    """Возвращает текущий курс base→target.

    Args:
        base: Исходная валюта, ISO-код (USD, EUR, RUB, CNY...)
        target: Целевая валюта, ISO-код

    Returns:
        Строка с курсом или сообщение об ошибке.
    """
    base = (base or "USD").upper().strip()
    target = (target or "RUB").upper().strip()

    try:
        rates = await _fetch_rates(base)
    except Exception as e:
        logger.exception("Ошибка получения курсов")
        return f"Не удалось получить курс {base}/{target}: {e}"

    if target not in rates:
        return f"Неизвестная валюта: {target}"

    rate = rates[target]
    return (
        f"Курс {base} → {target}: {rate:.4f} "
        f"(1 {base} = {rate:.4f} {target}). "
        f"Источник: open.er-api.com (актуальные рыночные данные)."
    )


@tool(
    name="get_exchange_rates_multi",
    description=(
        "Возвращает несколько курсов к рублю за один вызов "
        "(например, USD и EUR одновременно)."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
async def get_exchange_rates_multi(
    base: str = "USD,EUR", target: str = "RUB"
) -> str:
    """Возвращает несколько курсов к target.

    Args:
        base: Список ISO-кодов через запятую (USD,EUR,CNY)
        target: Целевая валюта

    Returns:
        Строка с несколькими курсами.
    """
    target = (target or "RUB").upper().strip()
    codes = [c.strip().upper() for c in (base or "").split(",") if c.strip()]

    lines: list[str] = [f"Курсы к {target} на сегодня:"]
    for code in codes:
        try:
            rates = await _fetch_rates(code)
            if target in rates:
                lines.append(f"• 1 {code} = {rates[target]:.4f} {target}")
            else:
                lines.append(f"• {code}: неизвестная валюта {target}")
        except Exception as e:
            lines.append(f"• {code}: ошибка ({e})")

    return "\n".join(lines)