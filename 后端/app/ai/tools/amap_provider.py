"""高德 A-class provider (《外部事实源与工具调用规范》二.1、三).

Real REST adapter exposing `RouteFact` / `WeatherFact` / `PoiFact` to the upper
layer (identical Schema to any MCP transport, so the business layer is unaware
of REST vs MCP). Key/URL/timeout/retry all come from `config`. The provider is
independently disabled when `AMAP_API_KEY` is empty. Failures never raise to the
business layer; they return `unknown` facts.

Note: REST is the implemented transport. `AMAP_MCP_ENABLED` is reserved for a
future MCP transport switch; the upper layer is agnostic either way.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import re
import threading
import time

import httpx

from app.ai.tools.schemas import PoiFact, RouteFact, WeatherFact
from app.core.config import settings

logger = logging.getLogger("travelplanet")

_DRIVING_PATH = "/v3/direction/driving"
_WALKING_PATH = "/v3/direction/walking"
_BICYCLING_PATH = "/v3/direction/bicycling"
_TRANSIT_PATH = "/v3/direction/transit"
_WEATHER_PATH = "/v3/weather/weatherInfo"
_POI_TEXT_PATH = "/v3/place/text"
_POI_AROUND_PATH = "/v3/place/around"
_GEOCODE_PATH = "/v3/geocode/geo"

_ROUTE_PATHS = {
    "driving": _DRIVING_PATH,
    "walking": _WALKING_PATH,
    "bicycling": _BICYCLING_PATH,
    "transit": _TRANSIT_PATH,
}
_AMAP_QPS_WINDOW_SECONDS = 1.0
_AMAP_REQUEST_TIMES: deque[float] = deque()
_AMAP_RATE_LOCK = threading.Lock()
_CITY_HINTS = [
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "武汉",
    "成都",
    "重庆",
    "南京",
    "苏州",
    "西安",
    "天津",
    "长沙",
    "郑州",
    "青岛",
    "厦门",
    "大理",
    "丽江",
    "昆明",
    "三亚",
    "汕头",
    "南澳",
]
_CITY_COORD_BOUNDS = [
    ("杭州", 118.3, 121.4, 29.1, 31.2),
    ("北京", 115.4, 117.6, 39.4, 41.1),
    ("上海", 120.8, 122.2, 30.6, 31.9),
    ("武汉", 113.7, 115.1, 29.9, 31.4),
    ("汕头", 116.2, 117.4, 23.0, 24.0),
]


@dataclass(frozen=True)
class GeocodeResult:
    location: str
    adcode: str | None = None
    city: str | None = None


def is_available() -> bool:
    """High德 is usable only when enabled and an API key is configured."""
    return bool(settings.TOOLS_ENABLED and settings.AMAP_API_KEY)


def _get(path: str, params: dict) -> dict | None:
    """Issue a GET to the 高德 REST API with timeout + bounded retry.

    Returns parsed JSON dict, or None on any failure (caller degrades).
    """
    url = f"{settings.AMAP_BASE_URL}{path}"
    query = {**params, "key": settings.AMAP_API_KEY}
    attempts = max(1, settings.TOOL_MAX_RETRY + 1)
    for attempt in range(attempts):
        try:
            _throttle_amap_qps()
            with httpx.Client(timeout=settings.TOOL_TIMEOUT_SECONDS) as client:
                resp = client.get(url, params=query)
            if resp.status_code != 200:
                logger.warning("amap %s http %d", path, resp.status_code)
                return None
            data = resp.json()
            if str(data.get("status")) != "1":
                logger.warning(
                    "amap %s status=%s info=%s",
                    path,
                    data.get("status"),
                    data.get("info"),
                )
                return None
            return data
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            logger.warning(
                "amap %s transient error attempt %d: %s",
                path,
                attempt + 1,
                type(exc).__name__,
            )
            continue
        except Exception:  # noqa: BLE001
            logger.exception("amap %s unexpected error", path)
            return None
    return None


def _throttle_amap_qps() -> None:
    """Keep all 高德 REST calls within the configured rolling-window QPS."""
    max_qps = max(1, int(getattr(settings, "AMAP_MAX_QPS", 3) or 3))
    while True:
        now = time.monotonic()
        with _AMAP_RATE_LOCK:
            while (
                _AMAP_REQUEST_TIMES
                and now - _AMAP_REQUEST_TIMES[0] >= _AMAP_QPS_WINDOW_SECONDS
            ):
                _AMAP_REQUEST_TIMES.popleft()
            if len(_AMAP_REQUEST_TIMES) < max_qps:
                _AMAP_REQUEST_TIMES.append(now)
                return
            wait_seconds = _AMAP_QPS_WINDOW_SECONDS - (now - _AMAP_REQUEST_TIMES[0])
        time.sleep(max(0.01, wait_seconds))


def weather(city: str, date: str) -> WeatherFact:
    """City-level weather (live/forecast). Returns `unknown` on failure."""
    data = _get(_WEATHER_PATH, {"city": city, "extensions": "all"})
    if data:
        forecasts = data.get("forecasts") or []
        if forecasts:
            casts = forecasts[0].get("casts") or []
            chosen = next(
                (c for c in casts if c.get("date") == date), casts[0] if casts else None
            )
            if chosen:
                summary = (
                    f"{chosen.get('dayweather', '')} "
                    f"{chosen.get('nighttemp', '')}-{chosen.get('daytemp', '')}℃"
                ).strip()
                return WeatherFact(city=city, date=date, summary=summary, status="ok")
    return WeatherFact(city=city, date=date, summary=None, status="unknown")


def poi_search(keyword: str, city: str | None = None) -> PoiFact:
    """Keyword POI search. Returns `unknown` on failure."""
    facts = poi_search_many(keyword, city=city, limit=1)
    return (
        facts[0]
        if facts
        else PoiFact(
            name=keyword, address=None, location=None, category=None, status="unknown"
        )
    )


def poi_search_many(
    keyword: str, city: str | None = None, limit: int = 8
) -> list[PoiFact]:
    """Keyword POI search. Returns up to ``limit`` facts, or one unknown fact."""
    params = {"keywords": keyword}
    if city:
        params["city"] = city
    data = _get(_POI_TEXT_PATH, params)
    if data:
        pois = data.get("pois") or []
        if pois:
            facts: list[PoiFact] = []
            for p in pois[: max(1, limit)]:
                facts.append(
                    PoiFact(
                        name=p.get("name", keyword),
                        address=p.get("address") or None,
                        location=p.get("location") or None,
                        category=p.get("type") or None,
                        status="ok",
                    )
                )
            return facts
    return [
        PoiFact(
            name=keyword, address=None, location=None, category=None, status="unknown"
        )
    ]


def poi_around(
    location: str,
    keywords: str,
    *,
    radius: int = 1000,
    city: str | None = None,
    limit: int = 20,
) -> list[PoiFact]:
    """Around POI search. ``location`` may be "lng,lat" or a place name."""
    city_hint = city or infer_city_hint(location)
    center = (
        normalize_coord(location)
        if looks_like_coord(location)
        else geocode(location, city=city_hint)
    )
    if not center:
        return [
            PoiFact(
                name=keywords,
                address=None,
                location=None,
                category=None,
                status="unknown",
            )
        ]
    params: dict[str, str | int] = {
        "location": center,
        "keywords": keywords,
        "radius": radius,
        "offset": max(1, min(limit, 20)),
    }
    if city_hint:
        params["city"] = city_hint
    data = _get(_POI_AROUND_PATH, params)
    if data:
        pois = data.get("pois") or []
        if pois:
            return [
                PoiFact(
                    name=p.get("name", keywords),
                    address=p.get("address") or None,
                    location=p.get("location") or None,
                    category=p.get("type") or None,
                    status="ok",
                )
                for p in pois[: max(1, min(limit, 20))]
            ]
    return [
        PoiFact(
            name=keywords,
            address=None,
            location=center,
            category=None,
            status="unknown",
        )
    ]


def route(
    origin: str,
    destination: str,
    origin_coord: str,
    dest_coord: str,
    *,
    mode: str = "driving",
    city: str | None = None,
) -> RouteFact:
    """Route between two coordinates ("lng,lat"). `unknown` on failure."""
    path = _ROUTE_PATHS.get(mode, _DRIVING_PATH)
    params = {"origin": origin_coord, "destination": dest_coord}
    if mode == "transit" and city:
        params["city"] = city
    data = _get(path, params)
    if data:
        item = _first_route_item(data, mode)
        if item:
            distance_m = _safe_float(item.get("distance"))
            duration_s = _safe_float(item.get("duration"))
            if distance_m or duration_s:
                return RouteFact(
                    origin=origin,
                    destination=destination,
                    mode=mode,  # type: ignore[arg-type]
                    distance_km=round(distance_m / 1000, 1) if distance_m else None,
                    duration_minutes=int(duration_s / 60) if duration_s else None,
                    status="ok",
                )
    return RouteFact(
        origin=origin,
        destination=destination,
        mode=mode,  # type: ignore[arg-type]
        distance_km=None,
        duration_minutes=None,
        status="unknown",
    )


def geocode(address: str, city: str | None = None) -> str | None:
    """Return "lng,lat" for an address, or None."""
    detail = geocode_detail(address, city=city)
    return detail.location if detail else None


def geocode_detail(address: str, city: str | None = None) -> GeocodeResult | None:
    """Return geocode location plus administrative hints, or None."""
    if looks_like_coord(address):
        return GeocodeResult(location=normalize_coord(address))
    city_hint = city or infer_city_hint(address)
    if _should_resolve_with_poi_first(address):
        poi_detail = _poi_location_detail(address, city=city_hint)
        if poi_detail:
            return poi_detail
    params = {"address": address}
    if city_hint:
        params["city"] = city_hint
    data = _get(_GEOCODE_PATH, params)
    if data:
        geocodes = data.get("geocodes") or []
        if geocodes:
            first = geocodes[0]
            location = first.get("location") or None
            if location:
                city_value = first.get("city")
                if isinstance(city_value, list):
                    city_value = None
                return GeocodeResult(
                    location=location,
                    adcode=first.get("adcode") or None,
                    city=city_value or None,
                )
    return _poi_location_detail(address, city=city_hint)


def _should_resolve_with_poi_first(address: str) -> bool:
    """Named commercial/transport POIs are safer through place/text than geocode."""
    text = (address or "").strip()
    if not text:
        return False
    if any(mark in text for mark in ("(", ")", "（", "）")):
        return True
    transport_markers = (
        "火车站",
        "高铁站",
        "动车站",
        "客运站",
        "客运中心",
        "汽车站",
        "地铁站",
        "交通枢纽",
        "机场",
        "航站楼",
        "码头",
        "港口",
    )
    if text.endswith("站") or any(marker in text for marker in transport_markers):
        return True
    commercial_markers = (
        "酒店",
        "宾馆",
        "旅馆",
        "客栈",
        "民宿",
        "公寓",
        "饭店",
        "餐厅",
        "火锅",
        "烧烤",
        "小吃",
        "便利店",
        "购物中心",
        "商场",
    )
    return any(marker in text for marker in commercial_markers)


def _poi_location_detail(keyword: str, city: str | None = None) -> GeocodeResult | None:
    params: dict[str, str | int] = {"keywords": keyword, "offset": 1}
    if city:
        params["city"] = city
    data = _get(_POI_TEXT_PATH, params)
    if not data:
        return None
    pois = data.get("pois") or []
    if not pois:
        return None
    first = pois[0]
    location = first.get("location") or None
    if not location:
        return None
    city_value = first.get("cityname") or first.get("city") or city
    if isinstance(city_value, list):
        city_value = city
    return GeocodeResult(
        location=location,
        adcode=first.get("adcode") or None,
        city=city_value or None,
    )


def _first_route_item(data: dict, mode: str) -> dict | None:
    route_data = data.get("route") or {}
    if mode == "transit":
        transits = route_data.get("transits") or []
        return transits[0] if transits else None
    paths = route_data.get("paths") or []
    return paths[0] if paths else None


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def looks_like_coord(value: str) -> bool:
    return bool(
        re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*", value or "")
    )


def normalize_coord(value: str) -> str:
    """Normalize a "lng,lat" coordinate string without geocoding it."""
    return ",".join(part.strip() for part in (value or "").split(",", 1))


def infer_city_hint(*texts: str | None) -> str | None:
    """Infer a coarse city hint from user/model supplied place text."""
    joined = " ".join(text for text in texts if text)
    for text in texts:
        city = _infer_city_from_coord(text)
        if city:
            return city
    for city in _CITY_HINTS:
        if city in joined:
            return "汕头" if city == "南澳" else city
    match = re.search(r"([\u4e00-\u9fa5]{2,8})(?:市|州)", joined)
    return match.group(1) if match else None


def _infer_city_from_coord(value: str | None) -> str | None:
    if not value or not looks_like_coord(value):
        return None
    lng_text, lat_text = normalize_coord(value).split(",", 1)
    try:
        lng = float(lng_text)
        lat = float(lat_text)
    except ValueError:
        return None
    for city, min_lng, max_lng, min_lat, max_lat in _CITY_COORD_BOUNDS:
        if min_lng <= lng <= max_lng and min_lat <= lat <= max_lat:
            return city
    return None
