"""和风天气 A-class (optional) provider (《外部事实源与工具调用规范》二.1).

Only used when 高德 weather does not satisfy a need. Returns `unknown` on any
failure; never raises to the business layer.
"""

from __future__ import annotations

import logging

import httpx

from app.ai.tools.schemas import WeatherFact
from app.core.config import settings

logger = logging.getLogger("travelplanet")


def is_available() -> bool:
    return bool(settings.TOOLS_ENABLED and settings.QWEATHER_API_KEY)


def forecast(city_id: str, city_name: str, date: str) -> WeatherFact:
    """3-day forecast lookup by location id. `unknown` on failure."""
    url = f"{settings.QWEATHER_BASE_URL}/v7/weather/3d"
    params = {"location": city_id, "key": settings.QWEATHER_API_KEY}
    try:
        with httpx.Client(timeout=settings.TOOL_TIMEOUT_SECONDS) as client:
            resp = client.get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            if str(data.get("code")) == "200":
                for day in data.get("daily", []):
                    if day.get("fxDate") == date:
                        summary = f"{day.get('textDay', '')} {day.get('tempMin', '')}-{day.get('tempMax', '')}℃".strip()
                        return WeatherFact(city=city_name, date=date, summary=summary, status="ok")
    except Exception:  # noqa: BLE001
        logger.exception("qweather forecast error")
    return WeatherFact(city=city_name, date=date, summary=None, status="unknown")
