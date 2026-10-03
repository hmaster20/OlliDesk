"""Инструмент погоды на базе Open-Meteo (бесплатный, без API-ключа)."""

import time

import httpx
from loguru import logger

from agents.tool_registry import tool
from core.config import AgentMode, ToolPolicy

# Кэш геокодинга (город -> координаты)
_GEOCODE_CACHE: dict[str, tuple[float, float, str]] = {}

# Кэш погоды (на 10 минут)
_WEATHER_CACHE: dict[str, tuple[float, str]] = {}

# Open-Meteo плохо ищет на кириллице — даём латинские алиасы для крупных городов.
_CITY_ALIASES: dict[str, str] = {
    "москва": "Moscow",
    "санкт-петербург": "Saint Petersburg",
    "спб": "Saint Petersburg",
    "питер": "Saint Petersburg",
    "новосибирск": "Novosibirsk",
    "екатеринбург": "Yekaterinburg",
    "казань": "Kazan",
    "нижний новгород": "Nizhny Novgorod",
    "челябинск": "Chelyabinsk",
    "самара": "Samara",
    "омск": "Omsk",
    "ростов-на-дону": "Rostov-on-Don",
    "уфа": "Ufa",
    "красноярск": "Krasnoyarsk",
    "воронеж": "Voronezh",
    "пермь": "Perm",
    "волгоград": "Volgograd",
    "краснодар": "Krasnodar",
    "саратов": "Saratov",
    "тюмень": "Tyumen",
    "тольятти": "Tolyatti",
    "ижевск": "Izhevsk",
    "барнаул": "Barnaul",
    "иркутск": "Irkutsk",
    "хабаровск": "Khabarovsk",
    "владивосток": "Vladivostok",
    "ярославль": "Yaroslavl",
    "махачкала": "Makhachkala",
    "томск": "Tomsk",
    "оренбург": "Orenburg",
    "кемерово": "Kemerovo",
    "новокузнецк": "Novokuznetsk",
    "рязань": "Ryazan",
    "астрахань": "Astrakhan",
    "набережные челны": "Naberezhnye Chelny",
    "пенза": "Penza",
    "липецк": "Lipetsk",
    "киров": "Kirov",
    "чебоксары": "Cheboksary",
    "тула": "Tula",
    "калининград": "Kaliningrad",
    "курск": "Kursk",
    "сочи": "Sochi",
    "ставрополь": "Stavropol",
    "улан-удэ": "Ulan-Ude",
    "тверь": "Tver",
    "магнитогорск": "Magnitogorsk",
    "иваново": "Ivanovo",
    "брянск": "Bryansk",
    "белгород": "Belgorod",
    "сургут": "Surgut",
    "владимир": "Vladimir",
    "архангельск": "Arkhangelsk",
    "смоленск": "Smolensk",
    "курган": "Kurgan",
    "орёл": "Orel",
    "орел": "Orel",
    "череповец": "Cherepovets",
    "вологда": "Vologda",
    "мурманск": "Murmansk",
    "тамбов": "Tambov",
    "стерлитамак": "Sterlitamak",
    "грозный": "Grozny",
    "якутск": "Yakutsk",
    "кострома": "Kostroma",
    "петрозаводск": "Petrozavodsk",
    "нижний тагил": "Nizhny Tagil",
    "йошкар-ола": "Yoshkar-Ola",
    "новороссийск": "Novorossiysk",
    "симферополь": "Simferopol",
    "севастополь": "Sevastopol",
    "минск": "Minsk",
    "киев": "Kyiv",
    "алматы": "Almaty",
    "астана": "Astana",
    "ташкент": "Tashkent",
    "бишкек": "Bishkek",
    "ереван": "Yerevan",
    "тбилиси": "Tbilisi",
    "баку": "Baku",
}

_HTTP_HEADERS = {
    "User-Agent": "OlliDesk/0.1 (+https://github.com/ollidesk)",
    "Accept": "application/json",
}

_WMO_CODES: dict[int, str] = {
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


async def _geocode_city(city: str) -> tuple[float, float, str] | None:
    """Геокодирует город через Open-Meteo Geocoding API.

    Пробует оригинальное название и его латинский алиас (для кириллицы).
    """
    cache_key = city.lower().strip()
    if cache_key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[cache_key]

    candidates: list[str] = []
    alias = _CITY_ALIASES.get(cache_key)
    if alias:
        candidates.append(alias)
    if city not in candidates:
        candidates.append(city)

    async with httpx.AsyncClient(headers=_HTTP_HEADERS, timeout=15.0) as client:
        for name in candidates:
            try:
                resp = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={
                        "name": name,
                        "count": 1,
                        "language": "ru",
                        "format": "json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results") or []
                if results:
                    r = results[0]
                    lat = float(r["latitude"])
                    lon = float(r["longitude"])
                    display_name = r.get("name", name)
                    country = r.get("country", "")
                    display = (
                        f"{display_name}, {country}" if country else display_name
                    )
                    _GEOCODE_CACHE[cache_key] = (lat, lon, display)
                    logger.debug(
                        f"Геокодинг '{city}' -> {display} ({lat},{lon})"
                    )
                    return lat, lon, display
            except Exception as e:
                logger.warning(f"Ошибка геокодинга '{name}': {e}")

    logger.warning(f"Город не найден: {city}")
    return None


@tool(
    name="get_weather",
    description=(
        "Получает ТЕКУЩУЮ (на сегодня) погоду в указанном городе. "
        "Используй, когда пользователь спрашивает про погоду, температуру, "
        "осадки в любом городе. Возвращает реальные данные Open-Meteo."
    ),
    policy=ToolPolicy.AUTO,
    modes=[AgentMode.CHAT, AgentMode.PLAN, AgentMode.AGENT],
)
async def get_weather(city: str) -> str:
    """Возвращает текущую погоду в городе.

    Args:
        city: Название города (например, "Москва", "Moscow")

    Returns:
        Текст с описанием погоды или сообщение об ошибке.
    """
    city = (city or "").strip()
    if not city:
        return "Ошибка: не указан город."

    cache_key = city.lower()
    if cache_key in _WEATHER_CACHE:
        ts, cached = _WEATHER_CACHE[cache_key]
        if time.time() - ts < 600:
            return cached

    geo = await _geocode_city(city)
    if geo is None:
        return (
            f"Не удалось определить координаты города «{city}». "
            "Попробуйте указать название латиницей (например, Moscow)."
        )

    lat, lon, display = geo

    try:
        async with httpx.AsyncClient(
            headers=_HTTP_HEADERS, timeout=15.0
        ) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": (
                        "temperature_2m,relative_humidity_2m,"
                        "apparent_temperature,weather_code,wind_speed_10m"
                    ),
                    "timezone": "auto",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        current = data.get("current") or {}
        temp = current.get("temperature_2m")
        feels = current.get("apparent_temperature")
        humidity = current.get("relative_humidity_2m")
        wind = current.get("wind_speed_10m")
        code = current.get("weather_code", 0)
        condition = _WMO_CODES.get(code, f"код {code}")

        if temp is None:
            return f"Open-Meteo не вернул температуру для {display}."

        result = (
            f"Погода в {display} (сейчас):\n"
            f"• {condition}\n"
            f"• Температура: {temp}°C (ощущается как {feels}°C)\n"
            f"• Влажность: {humidity}%\n"
            f"• Ветер: {wind} км/ч"
        )
        _WEATHER_CACHE[cache_key] = (time.time(), result)
        return result

    except httpx.HTTPError as e:
        logger.exception("Open-Meteo HTTP error")
        return f"Сервис погоды недоступен: {type(e).__name__}: {e}"
    except Exception as e:
        logger.exception("Open-Meteo error")
        return f"Не удалось получить погоду: {type(e).__name__}: {e}"