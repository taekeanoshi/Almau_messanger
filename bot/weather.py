"""Прогноз погоды на день похода через Open-Meteo (бесплатный API без ключа).

Прогноз приблизительный: он считается для района Заилийского Алатау, а в горах на высоте
холоднее и ветренее. Окончательное решение о выходе всегда за организатором.
"""
import time
from dataclasses import dataclass
from datetime import date

import aiohttp

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
DEFAULT_LAT, DEFAULT_LON = 43.10, 77.05   # район Заилийского Алатау, ориентировочно
MAX_DAYS_AHEAD = 15                       # дальше Open-Meteo прогноз не даёт
CACHE_SECONDS = 30 * 60

# пороги предупреждений — ориентиры бота, не заменяют решение организатора
WIND_WARN_MS = 10.0
PRECIP_WARN_MM = 5.0
COLD_WARN_C = -10.0

_cache: dict[tuple, tuple[float, dict]] = {}


class ForecastError(Exception):
    """Прогноз недоступен. Текст сообщения можно показывать пользователю."""


@dataclass(frozen=True)
class Forecast:
    day: date
    summary: str
    tmin: float
    tmax: float
    precip: float
    wind: float
    code: int


def describe_code(code: int) -> str:
    """Код погоды WMO → короткое описание."""
    if code == 0:
        return "ясно"
    if code in (1, 2, 3):
        return "переменная облачность"
    if code in (45, 48):
        return "туман"
    if 51 <= code <= 57:
        return "морось"
    if 61 <= code <= 67:
        return "дождь"
    if 71 <= code <= 77 or code in (85, 86):
        return "снег"
    if 80 <= code <= 82:
        return "ливни"
    if code >= 95:
        return "гроза"
    return "облачно"


async def fetch_json(url: str, params: dict) -> dict:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_forecast(lat: float | None, lon: float | None, day: date, today: date) -> Forecast:
    ahead = (day - today).days
    if ahead < 0:
        raise ForecastError("Поход уже прошёл.")
    if ahead > MAX_DAYS_AHEAD:
        raise ForecastError(f"Прогноз появится за {MAX_DAYS_AHEAD} дней до похода. Загляните позже.")

    lat, lon = lat if lat is not None else DEFAULT_LAT, lon if lon is not None else DEFAULT_LON
    key = (round(lat, 2), round(lon, 2), today)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        data = cached[1]
    else:
        params = {
            "latitude": lat, "longitude": lon, "timezone": "Asia/Almaty", "wind_speed_unit": "ms",
            "forecast_days": MAX_DAYS_AHEAD + 1,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
        }
        try:
            data = await fetch_json(OPEN_METEO, params)
        except Exception as exc:
            raise ForecastError("Прогноз сейчас недоступен. Попробуйте позже.") from exc
        _cache[key] = (time.monotonic(), data)

    try:
        daily = data["daily"]
        i = daily["time"].index(day.isoformat())
        code = int(daily["weather_code"][i])
        return Forecast(
            day=day, summary=describe_code(code), tmin=float(daily["temperature_2m_min"][i]),
            tmax=float(daily["temperature_2m_max"][i]), precip=float(daily["precipitation_sum"][i] or 0),
            wind=float(daily["wind_speed_10m_max"][i]), code=code,
        )
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        raise ForecastError("Прогноз на эту дату пока недоступен.") from exc


def risk_notes(f: Forecast) -> list[str]:
    notes = []
    if f.code >= 95:
        notes.append("гроза: в горах это опасно")
    if f.wind >= WIND_WARN_MS:
        notes.append(f"сильный ветер, до {f.wind:.0f} м/с")
    if f.precip >= PRECIP_WARN_MM:
        notes.append(f"заметные осадки, около {f.precip:.0f} мм")
    if f.tmax <= COLD_WARN_C:
        notes.append("очень холодно")
    return notes


def forecast_text(f: Forecast) -> str:
    text = (f"🌤 Прогноз на {f.day:%d.%m}: {f.summary}, от {f.tmin:.0f}° до {f.tmax:.0f}°C, "
            f"ветер до {f.wind:.0f} м/с, осадки {f.precip:.0f} мм.")
    notes = risk_notes(f)
    if notes:
        text += "\n⚠️ Условия непростые: " + "; ".join(notes) + ". Уточните у организатора, состоится ли поход."
    text += "\nПрогноз для района Заилийского Алатау: на высоте холоднее и ветренее."
    return text
