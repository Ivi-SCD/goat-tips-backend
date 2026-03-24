"""
Weather Service
===============
Fetches match-time weather from Open-Meteo API (free, no key required).
Used to adjust goal-rate predictions (rain/wind reduce scoring ~5-15%).

API: https://api.open-meteo.com/v1/forecast
WMO weather codes: 0=clear, 1-3=cloudy, 45-48=fog, 51-57=drizzle,
                   61-67=rain, 71-77=snow, 80-82=showers, 95-99=thunderstorm
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

STADIUM_COORDS: dict[str, tuple[float, float]] = {
    "Emirates Stadium":                    (51.5549,  -0.1084),
    "Stamford Bridge":                     (51.4817,  -0.1910),
    "Anfield":                             (53.4308,  -2.9609),
    "Old Trafford":                        (53.4631,  -2.2913),
    "Etihad Stadium":                      (53.4831,  -2.2004),
    "Tottenham Hotspur Stadium":           (51.6042,  -0.0662),
    "White Hart Lane":                     (51.6042,  -0.0662),
    "Villa Park":                          (52.5092,  -1.8845),
    "London Stadium":                      (51.5387,  -0.0166),
    "Goodison Park":                       (53.4388,  -2.9661),
    "St. James Park":                      (54.9756,  -1.6218),
    "Selhurst Park":                       (51.3983,  -0.0855),
    "King Power Stadium":                  (52.6204,  -1.1422),
    "Molineux Stadium":                    (52.5903,  -2.1302),
    "Vitality Stadium":                    (50.7352,  -1.8384),
    "Craven Cottage":                      (51.4749,  -0.2217),
    "Brentford Community Stadium":         (51.4882,  -0.2888),
    "American Express Community Stadium":  (50.8618,  -0.0837),
    "Carrow Road":                         (52.6219,   1.3094),
    "St. Mary's Stadium":                  (50.9058,  -1.3913),
    "Kenilworth Road":                     (51.8842,  -0.4317),
    "Portman Road":                        (52.0544,   1.1444),
    "Bramall Lane":                        (53.3703,  -1.4706),
    "Turf Moor":                           (53.7887,  -2.2300),
    "bet365 Stadium":                      (53.0005,  -2.1756),
    "Stadium of Light":                    (54.9142,  -1.3880),
    "The Hawthorns":                       (52.5090,  -1.9637),
    "Wembley Stadium":                     (51.5560,  -0.2796),
    "John Smith's Stadium":                (53.6543,  -1.7693),
    "Swansea.com Stadium":                 (51.6435,  -3.9344),
    "Cardiff City Stadium":                (51.4733,  -3.2033),
    "Riverside":                           (54.5784,  -1.2184),
    "MKM Stadium":                         (53.7459,  -0.3674),
}

CITY_COORDS: dict[str, tuple[float, float]] = {
    "london":           (51.5074,  -0.1278),
    "manchester":       (53.4808,  -2.2426),
    "liverpool":        (53.4084,  -2.9916),
    "birmingham":       (52.4862,  -1.8904),
    "newcastle":        (54.9783,  -1.6178),
    "leicester":        (52.6369,  -1.1398),
    "bournemouth":      (50.7192,  -1.8808),
    "wolverhampton":    (52.5862,  -2.1283),
    "brighton":         (50.8229,  -0.1363),
    "falmer":           (50.8618,  -0.0837),
    "norwich":          (52.6309,   1.2974),
    "southampton":      (50.9097,  -1.4044),
    "luton":            (51.8787,  -0.4200),
    "ipswich":          (52.0567,   1.1482),
    "sheffield":        (53.3810,  -1.4701),
    "burnley":          (53.7887,  -2.2300),
    "stoke":            (52.9883,  -2.1722),
    "sunderland":       (54.9142,  -1.3880),
    "west bromwich":    (52.5090,  -1.9637),
    "west brom":        (52.5090,  -1.9637),
    "huddersfield":     (53.6543,  -1.7693),
    "swansea":          (51.6435,  -3.9344),
    "cardiff":          (51.4733,  -3.2033),
    "middlesbrough":    (54.5784,  -1.2184),
    "hull":             (53.7459,  -0.3674),
    "watford":          (51.6498,  -0.4003),
}


@dataclass
class WeatherCondition:
    weather_code: int
    description: str
    precipitation_mm: float
    wind_speed_kmh: float
    temperature_c: float
    condition_label: str   # "clear" | "cloudy" | "drizzle" | "rain" | "snow" | "storm"
    goal_factor: float     # Multiplicador de λ (< 1.0 reduz gols esperados)
    source: str            # "stadium" | "city" | "unavailable"


_WMO_LABEL: list[tuple[range, str]] = [
    (range(0, 4),   "clear"),
    (range(4, 50),  "cloudy"),
    (range(50, 58), "drizzle"),
    (range(58, 68), "rain"),
    (range(68, 78), "snow"),
    (range(78, 84), "cloudy"),
    (range(80, 83), "rain"),
    (range(83, 90), "snow"),
    (range(90, 100),"storm"),
]


def _wmo_label(code: int) -> str:
    for r, label in _WMO_LABEL:
        if code in r:
            return label
    return "cloudy"


def _goal_factor(label: str, precipitation_mm: float, wind_kmh: float) -> float:
    """Goal rate multiplier based on weather. Derived from literature (~0.15 goals reduction in heavy rain/wind)."""
    factor = 1.0
    if label == "drizzle":
        factor -= 0.04
    elif label == "rain":
        factor -= 0.08 + min(precipitation_mm * 0.01, 0.05)  # up to -0.13 in heavy rain
    elif label == "snow":
        factor -= 0.12
    elif label == "storm":
        factor -= 0.15
    if wind_kmh > 40:
        factor -= 0.05
    elif wind_kmh > 25:
        factor -= 0.02
    return round(max(factor, 0.75), 3)  # floor at -25%


def _find_coords(stadium_name: Optional[str], city: Optional[str]) -> Optional[tuple[float, float]]:
    if stadium_name:
        # Exact match
        if stadium_name in STADIUM_COORDS:
            return STADIUM_COORDS[stadium_name]
        # Partial match
        sn_lower = stadium_name.lower()
        for k, v in STADIUM_COORDS.items():
            if k.lower() in sn_lower or sn_lower in k.lower():
                return v
    if city:
        city_lower = city.lower().strip()
        if city_lower in CITY_COORDS:
            return CITY_COORDS[city_lower]
        for k, v in CITY_COORDS.items():
            if k in city_lower:
                return v
    return None


async def get_match_weather(
    stadium_name: Optional[str] = None,
    city: Optional[str] = None,
    match_hour_utc: Optional[int] = None,
) -> Optional[WeatherCondition]:
    """
    Fetch weather for a match venue from Open-Meteo.

    Args:
        stadium_name: PL stadium name (e.g. "Emirates Stadium")
        city: Fallback city name (e.g. "London")
        match_hour_utc: Hour of kickoff in UTC (0-23). If None, uses current time.

    Returns:
        WeatherCondition with goal_factor multiplier, or None on failure.
    """
    coords = _find_coords(stadium_name, city)
    if not coords:
        logger.debug("Weather: no coords for stadium=%s city=%s", stadium_name, city)
        return None

    lat, lon = coords
    source = "stadium" if (stadium_name and stadium_name in STADIUM_COORDS) else "city"

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=weather_code,precipitation,wind_speed_10m,temperature_2m"
        f"&current=weather_code,precipitation,wind_speed_10m,temperature_2m"
        f"&timezone=UTC&forecast_days=2"
    )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Weather API failed: %s", exc)
        return None

    # Use current conditions or the specific match hour
    if match_hour_utc is not None and "hourly" in data:
        times = data["hourly"]["time"]
        # Find the hour index closest to match time today
        now = datetime.now(timezone.utc)
        target = f"{now.strftime('%Y-%m-%d')}T{match_hour_utc:02d}:00"
        if target not in times:
            # Try tomorrow
            from datetime import timedelta
            tomorrow = (now + timedelta(days=1)).strftime('%Y-%m-%d')
            target = f"{tomorrow}T{match_hour_utc:02d}:00"

        if target in times:
            idx = times.index(target)
            wc   = data["hourly"]["weather_code"][idx]
            prec = data["hourly"]["precipitation"][idx]
            wind = data["hourly"]["wind_speed_10m"][idx]
            temp = data["hourly"]["temperature_2m"][idx]
        else:
            # Fallback to current
            cur = data.get("current", {})
            wc   = cur.get("weather_code", 0)
            prec = cur.get("precipitation", 0.0)
            wind = cur.get("wind_speed_10m", 0.0)
            temp = cur.get("temperature_2m", 15.0)
    else:
        cur = data.get("current", {})
        wc   = cur.get("weather_code", 0)
        prec = cur.get("precipitation", 0.0)
        wind = cur.get("wind_speed_10m", 0.0)
        temp = cur.get("temperature_2m", 15.0)

    label = _wmo_label(wc)
    factor = _goal_factor(label, prec, wind)

    # Human-readable description
    _DESCRIPTIONS = {
        "clear":   "Céu limpo",
        "cloudy":  "Nublado",
        "drizzle": "Garoa",
        "rain":    "Chuva",
        "snow":    "Neve",
        "storm":   "Trovoada",
    }

    return WeatherCondition(
        weather_code=wc,
        description=_DESCRIPTIONS.get(label, label),
        precipitation_mm=round(prec, 1),
        wind_speed_kmh=round(wind, 1),
        temperature_c=round(temp, 1),
        condition_label=label,
        goal_factor=factor,
        source=source,
    )
