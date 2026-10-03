"""Инструмент погоды на базе Open-Meteo (бесплатный, без API-ключа)."""

from typing import Any

import httpx
from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy

# Кэш геокодинга (город -> координаты)
_GEOCODE_CACHE: dict[str, tuple[float, float, str]] = {}

# Кэш погоды (на 10 минут)
_WEATHER_CACHE: dict[str, tuple[float, str]] = {}


async def _geocode_city(city: str) -> tuple[float, float, str] | None:
    """Геокодирует город через Open-Meteo Geocoding API.

    Returns:
        (latitude, longitude, display_name) или None
    """
    cache_key = city.lower().strip()
    if cache_key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[cache_key]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": city,
                    "count": 1,
                    "language": "ru",
                    "format": "json",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        if not results:
            return None

        r = results[0]
        lat = r["latitude"]
        lon = r["longitude"]
        name = r.get("name", city)
        country = r.get("country", "")

        display = f"{name}, {country}" if country else name
        _GEOCODE_CACHE[cache_key] = (lat, lon, display)
        return lat, lon, display

    except Exception as e:
        logger.warning(f"Ошибка геокодинга {city}: {e}")
        return None


# WMO Weather interpretation codes
_WMO_CODES = {
    0: "Ясно",
    1: "Преимущественно ясно",
    2: "Переменная облачность",
    3: "Пасмурно",
    45: "Туман",
    48: "Изморозь",
    51: "Слабая морось",
    53: "Умеренная морось",
    55: "Сильная морось",
    56: "Слабая ледяная морось",
    57: "Сильная ледяная морось",
    61: "Слабый дождь",
    63: "Умеренный дождь",
    65: "Сильный дождь",
    66: "Слабый ледяной дождь",
    67: "Сильный ледяной дождь",
    71: "Слабый снегопад",
    73: "Умеренный снегопад",
    75: "Сильный снегопад",
    77: "Снежные зёрна",
    80: "Слабые ливни",
    81: "Умеренные ливни",
    82: "Сильные ливни",
    85: "Слабый снегопад с ливнями",
    86: "Сильный снегопад с ливнями",
    95: "Гроза",
    96: "Гроза с небольшим градом",
    99: "Гроза с сильным градом",
}


@tool(
    name="get_weather",
    description="Получает текущую погоду в указанном городе",
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
async def get_weather(city: str) -> str:
    """Возвращает текущую погоду в городе.

    Args:
        city: Название города (например, "Москва")

    Returns:
        Текст с описанием погоды
    """
    import time

    # Проверяем кэш (10 минут)
    cache_key = city.lower().strip()
    if cache_key in _WEATHER_CACHE:
        ts, cached = _WEATHER_CACHE[cache_key]
        if time.time() - ts < 600:
            return cached

    # Геокодинг
    geo = await _geocode_city(city)
    if geo is None:
        return f"Не удалось найти город «{city}». Попробуйте другое название."

    lat, lon, display = geo

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,"
                               "apparent_temperature,weather_code,wind_speed_10m",
                    "timezone": "auto",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

        current = data.get("current", {})
        temp = current.get("temperature_2m", "?")
        feels = current.get("apparent_temperature", "?")
        humidity = current.get("relative_humidity_2m", "?")
        wind = current.get("wind_speed_10m", "?")
        code = current.get("weather_code", 0)
        condition = _WMO_CODES.get(code, f"Код {code}")

        result = (
            f"Погода в {display}:\n"
            f"• {condition}\n"
            f"• Температура: {temp}°C (ощущается как {feels}°C)\n"
            f"• Влажность: {humidity}%\n"
            f"• Ветер: {wind} км/ч"
        )

        _WEATHER_CACHE[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        logger.error(f"Ошибка получения погоды: {e}")
        return f"Не удалось получить погоду для «{city}»: {e}"