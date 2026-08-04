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

from collections import defaultdict, deque
from dataclasses import dataclass
import logging
import re
import threading
import time
from datetime import date, timedelta

import httpx

from app.ai.tools.schemas import (
    PoiFact,
    RouteAlternativeFact,
    RouteFact,
    RouteStepFact,
    WeatherFact,
)
from app.core.config import settings

logger = logging.getLogger("travelplanet")

_DRIVING_PATH = "/v5/direction/driving"
_WALKING_PATH = "/v5/direction/walking"
_BICYCLING_PATH = "/v5/direction/bicycling"
_TRANSIT_PATH = "/v5/direction/transit/integrated"
_WEATHER_PATH = "/v3/weather/weatherInfo"
_POI_TEXT_PATH = "/v5/place/text"
_POI_AROUND_PATH = "/v5/place/around"
_POI_DETAIL_PATH = "/v5/place/detail"
_GEOCODE_PATH = "/v3/geocode/geo"
_REGEOCODE_PATH = "/v3/geocode/regeo"
_DISTRICT_PATH = "/v3/config/district"

_ROUTE_PATHS = {
    "driving": _DRIVING_PATH,
    "walking": _WALKING_PATH,
    "bicycling": _BICYCLING_PATH,
    "transit": _TRANSIT_PATH,
}
_AMAP_QPS_WINDOW_SECONDS = 1.0
_AMAP_REQUEST_TIMES: dict[str, deque[float]] = defaultdict(deque)
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
    citycode: str | None = None
    district: str | None = None
    formatted_address: str | None = None


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
            _throttle_amap_qps(path)
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


def _throttle_amap_qps(service_key: str) -> None:
    """Keep each AMap service within its screenshot-confirmed 3 QPS limit."""
    # The active console screenshot confirms 3 QPS for every service. The env
    # may lower this safety cap, but cannot raise it beyond the purchased tier.
    max_qps = min(3, max(1, int(getattr(settings, "AMAP_MAX_QPS", 3) or 3)))
    with _AMAP_RATE_LOCK:
        request_times = _AMAP_REQUEST_TIMES[service_key]
    while True:
        now = time.monotonic()
        with _AMAP_RATE_LOCK:
            while (
                request_times
                and now - request_times[0] >= _AMAP_QPS_WINDOW_SECONDS
            ):
                request_times.popleft()
            if len(request_times) < max_qps:
                request_times.append(now)
                return
            wait_seconds = _AMAP_QPS_WINDOW_SECONDS - (now - request_times[0])
        time.sleep(max(0.01, wait_seconds))


def weather(city: str, date: str) -> WeatherFact:
    """City-level weather (live/forecast). Returns `unknown` on failure."""
    data = _get(_WEATHER_PATH, {"city": city, "extensions": "all"})
    if data:
        forecasts = data.get("forecasts") or []
        if forecasts:
            casts = forecasts[0].get("casts") or []
            chosen = next((c for c in casts if c.get("date") == date), None)
            if chosen:
                return _weather_fact(
                    city,
                    chosen,
                    report_time=forecasts[0].get("reporttime"),
                )
    return WeatherFact(city=city, date=date, summary=None, status="unknown")


def weather_range(
    city: str,
    start_date: str,
    end_date: str,
    *,
    display_city: str | None = None,
) -> list[WeatherFact]:
    """Fetch one official short-forecast payload for the requested date range."""
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return [
            WeatherFact(
                city=display_city or city,
                date=start_date,
                summary=None,
                status="unknown",
            )
        ]
    if end < start:
        start, end = end, start
    end = min(end, start + timedelta(days=13))
    requested = [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    ]
    data = _get(_WEATHER_PATH, {"city": city, "extensions": "all"})
    casts_by_date: dict[str, dict] = {}
    report_time: str | None = None
    if data:
        forecasts = data.get("forecasts") or []
        if forecasts:
            report_time = forecasts[0].get("reporttime") or None
            casts_by_date = {
                str(item.get("date")): item
                for item in (forecasts[0].get("casts") or [])
                if item.get("date")
            }
    facts: list[WeatherFact] = []
    for day in requested:
        item = casts_by_date.get(day)
        if item:
            facts.append(
                _weather_fact(display_city or city, item, report_time=report_time)
            )
        else:
            facts.append(
                WeatherFact(
                    city=display_city or city,
                    date=day,
                    summary=None,
                    status="unknown",
                    report_time=report_time,
                )
            )
    return facts


def _weather_fact(
    city: str,
    item: dict,
    *,
    report_time: str | None,
) -> WeatherFact:
    day_weather = _optional_text(item.get("dayweather"))
    night_weather = _optional_text(item.get("nightweather"))
    day_temp = _optional_float(item.get("daytemp"))
    night_temp = _optional_float(item.get("nighttemp"))
    weather_text = (
        day_weather
        if not night_weather or night_weather == day_weather
        else f"白天{day_weather or '未知'}、夜间{night_weather}"
    )
    temp_text = (
        f"{_format_number(night_temp)}-{_format_number(day_temp)}℃"
        if day_temp is not None and night_temp is not None
        else ""
    )
    summary = " ".join(part for part in (weather_text, temp_text) if part).strip()
    return WeatherFact(
        city=city,
        date=str(item.get("date") or ""),
        summary=summary or None,
        status="ok",
        day_weather=day_weather,
        night_weather=night_weather,
        day_temp_c=day_temp,
        night_temp_c=night_temp,
        day_wind=_optional_text(item.get("daywind")),
        night_wind=_optional_text(item.get("nightwind")),
        day_power=_optional_text(item.get("daypower")),
        night_power=_optional_text(item.get("nightpower")),
        report_time=report_time,
    )


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
    keyword: str | None,
    city: str | None = None,
    limit: int = 12,
    *,
    types: str | None = None,
    city_limit: bool = True,
) -> list[PoiFact]:
    """POI 2.0 keyword/type search with rich travel-facing fields."""
    safe_limit = max(1, min(limit, 25))
    params: dict[str, str | int] = {
        "page_size": safe_limit,
        "page_num": 1,
        "show_fields": "business,navi,photos",
    }
    if keyword:
        params["keywords"] = keyword
    if types:
        params["types"] = types
    if city:
        params["region"] = city
        params["city_limit"] = "true" if city_limit else "false"
    data = _get(_POI_TEXT_PATH, params)
    if data:
        pois = data.get("pois") or []
        if pois:
            return [
                _poi_fact_from_payload(p, fallback_name=keyword or types or "POI")
                for p in pois[:safe_limit]
            ]
    return [
        PoiFact(
            name=keyword or types or "POI",
            address=None,
            location=None,
            category=None,
            status="unknown",
        )
    ]


def poi_around(
    location: str,
    keyword: str | None,
    *,
    radius: int = 1000,
    city: str | None = None,
    limit: int = 12,
    types: str | None = None,
    city_limit: bool = True,
    sort_rule: str = "distance",
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
                name=keyword or types or "POI",
                address=None,
                location=None,
                category=None,
                status="unknown",
            )
        ]
    safe_limit = max(1, min(limit, 25))
    params: dict[str, str | int] = {
        "location": center,
        "radius": radius,
        "page_size": safe_limit,
        "page_num": 1,
        "sortrule": sort_rule if sort_rule in {"distance", "weight"} else "distance",
        "show_fields": "business,navi,photos",
    }
    if keyword:
        params["keywords"] = keyword
    if types:
        params["types"] = types
    if city_hint:
        params["region"] = city_hint
        params["city_limit"] = "true" if city_limit else "false"
    data = _get(_POI_AROUND_PATH, params)
    if data:
        pois = data.get("pois") or []
        if pois:
            return [
                _poi_fact_from_payload(p, fallback_name=keyword or types or "POI")
                for p in pois[:safe_limit]
            ]
    return [
        PoiFact(
            name=keyword or types or "POI",
            address=None,
            location=center,
            category=None,
            status="unknown",
        )
    ]


def _poi_fact_from_payload(payload: dict, *, fallback_name: str) -> PoiFact:
    business = payload.get("business") if isinstance(payload.get("business"), dict) else {}
    navi = payload.get("navi") if isinstance(payload.get("navi"), dict) else {}
    photos = payload.get("photos") if isinstance(payload.get("photos"), list) else []
    first_photo = photos[0] if photos and isinstance(photos[0], dict) else {}
    return PoiFact(
        name=_optional_text(payload.get("name")) or fallback_name,
        address=_optional_text(payload.get("address")),
        location=_optional_text(payload.get("location")),
        category=_optional_text(payload.get("type")),
        status="ok",
        poi_id=_optional_text(payload.get("id")),
        parent_id=_optional_text(payload.get("parent")),
        typecode=_optional_text(payload.get("typecode")),
        adcode=_optional_text(payload.get("adcode")),
        citycode=_optional_text(payload.get("citycode")),
        city_name=_optional_text(payload.get("cityname")),
        district_name=_optional_text(payload.get("adname")),
        distance_m=_optional_int(payload.get("distance")),
        business_area=_optional_text(business.get("business_area")),
        opening_hours_today=_optional_text(business.get("opentime_today")),
        opening_hours_week=_optional_text(business.get("opentime_week")),
        tel=_optional_text(business.get("tel")),
        tag=_optional_text(business.get("tag")),
        rating=_optional_float(business.get("rating")),
        cost_yuan=_optional_float(business.get("cost")),
        entrance_location=_optional_text(navi.get("entr_location")),
        exit_location=_optional_text(navi.get("exit_location")),
        photo_url=_optional_text(first_photo.get("url")),
    )


def route(
    origin: str,
    destination: str,
    origin_coord: str,
    dest_coord: str,
    *,
    mode: str = "driving",
    origin_poi_id: str | None = None,
    destination_poi_id: str | None = None,
    origin_adcode: str | None = None,
    destination_adcode: str | None = None,
    origin_citycode: str | None = None,
    destination_citycode: str | None = None,
    strategy: int | None = None,
    alternatives: int = 3,
    waypoint_locations: list[str] | None = None,
    destination_type: str | None = None,
    vehicle_plate: str | None = None,
    car_type: int = 0,
    avoid_ferry: bool = False,
    night_service: bool = False,
) -> RouteFact:
    """AMap route 2.0 between two coordinates with compact alternatives."""
    path = _ROUTE_PATHS.get(mode, _DRIVING_PATH)
    params: dict[str, str | int] = {
        "origin": origin_coord,
        "destination": dest_coord,
        "show_fields": "cost,navi,polyline",
    }
    requested_alternatives = max(1, alternatives)
    if mode == "driving":
        params["strategy"] = strategy if strategy is not None else 32
        clean_waypoints = [
            normalize_coord(value)
            for value in (waypoint_locations or [])
            if looks_like_coord(value)
        ][:16]
        if clean_waypoints:
            params["waypoints"] = ";".join(clean_waypoints)
        if destination_type:
            params["destination_type"] = destination_type
        if vehicle_plate:
            params["plate"] = vehicle_plate
        params["cartype"] = car_type if car_type in {0, 1, 2} else 0
        params["ferry"] = 1 if avoid_ferry else 0
        if origin_poi_id:
            params["origin_id"] = origin_poi_id
        if destination_poi_id:
            params["destination_id"] = destination_poi_id
    elif mode in {"walking", "bicycling"}:
        params["alternative_route"] = min(requested_alternatives, 3)
        if mode == "walking":
            if origin_poi_id:
                params["origin_id"] = origin_poi_id
            if destination_poi_id:
                params["destination_id"] = destination_poi_id
    elif mode == "transit":
        if not origin_citycode or not destination_citycode:
            return _unknown_route(
                origin,
                destination,
                mode,
                origin_coord,
                dest_coord,
                origin_poi_id,
                destination_poi_id,
                strategy,
            )
        params["city1"] = origin_citycode
        params["city2"] = destination_citycode
        params["strategy"] = strategy if strategy is not None else 0
        params["AlternativeRoute"] = min(requested_alternatives, 10)
        params["nightflag"] = 1 if night_service else 0
        if origin_poi_id and destination_poi_id:
            params["originpoi"] = origin_poi_id
            params["destinationpoi"] = destination_poi_id
        if origin_adcode:
            params["ad1"] = origin_adcode
        if destination_adcode:
            params["ad2"] = destination_adcode
    data = _get(path, params)
    if data:
        route_data = data.get("route") if isinstance(data.get("route"), dict) else {}
        items = route_data.get("transits" if mode == "transit" else "paths") or []
        parsed = [
            _route_alternative(item, mode, route_data, include_polyline=index == 0)
            for index, item in enumerate(items[: min(requested_alternatives, 10)])
            if isinstance(item, dict)
        ]
        parsed = [item for item in parsed if item.distance_km or item.duration_minutes]
        if parsed:
            first = parsed[0]
            return RouteFact(
                origin=origin,
                destination=destination,
                mode=mode,  # type: ignore[arg-type]
                distance_km=first.distance_km,
                duration_minutes=first.duration_minutes,
                status="ok",
                origin_location=origin_coord,
                destination_location=dest_coord,
                origin_poi_id=origin_poi_id,
                destination_poi_id=destination_poi_id,
                waypoint_locations=(
                    clean_waypoints if mode == "driving" else []
                ),
                strategy=(
                    strategy
                    if strategy is not None
                    else (0 if mode == "transit" else 32 if mode == "driving" else None)
                ),
                alternatives=parsed,
            )
    return _unknown_route(
        origin,
        destination,
        mode,
        origin_coord,
        dest_coord,
        origin_poi_id,
        destination_poi_id,
        strategy,
    )


def _unknown_route(
    origin: str,
    destination: str,
    mode: str,
    origin_coord: str,
    dest_coord: str,
    origin_poi_id: str | None,
    destination_poi_id: str | None,
    strategy: int | None,
) -> RouteFact:
    return RouteFact(
        origin=origin,
        destination=destination,
        mode=mode,  # type: ignore[arg-type]
        distance_km=None,
        duration_minutes=None,
        status="unknown",
        origin_location=origin_coord,
        destination_location=dest_coord,
        origin_poi_id=origin_poi_id,
        destination_poi_id=destination_poi_id,
        strategy=strategy,
    )


def _route_alternative(
    item: dict,
    mode: str,
    route_data: dict,
    *,
    include_polyline: bool,
) -> RouteAlternativeFact:
    cost = item.get("cost") if isinstance(item.get("cost"), dict) else {}
    duration_s = _safe_float(cost.get("duration") or item.get("duration"))
    steps = (
        _transit_steps(item.get("segments") or [])
        if mode == "transit"
        else _ordinary_route_steps(item.get("steps") or [])
    )
    polyline = _join_polylines(steps) if include_polyline else None
    raw_cost = cost.get("transit_fee")
    if raw_cost is None and not isinstance(item.get("cost"), dict):
        raw_cost = item.get("cost")
    return RouteAlternativeFact(
        distance_km=_km(item.get("distance")),
        duration_minutes=_minutes(duration_s),
        walking_distance_km=_km(item.get("walking_distance")),
        transfers=_transit_transfer_count(item) if mode == "transit" else None,
        cost_yuan=_optional_float(raw_cost),
        taxi_cost_yuan=_optional_float(
            cost.get("taxi_fee") or cost.get("taxi") or route_data.get("taxi_cost")
        ),
        tolls_yuan=_optional_float(cost.get("tolls")),
        toll_distance_km=_km(cost.get("toll_distance")),
        traffic_lights=_optional_int(cost.get("traffic_lights")),
        night_service=(str(item.get("nightflag")) == "1") if mode == "transit" else None,
        restriction=_optional_text(item.get("restriction")),
        polyline=polyline,
        steps=steps[:24],
    )


def _ordinary_route_steps(raw_steps: list) -> list[RouteStepFact]:
    steps: list[RouteStepFact] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        cost = raw.get("cost") if isinstance(raw.get("cost"), dict) else {}
        steps.append(
            RouteStepFact(
                instruction=_optional_text(raw.get("instruction")),
                road_name=_optional_text(raw.get("road_name") or raw.get("road")),
                distance_km=_km(raw.get("step_distance") or raw.get("distance")),
                duration_minutes=_minutes(
                    _safe_float(cost.get("duration") or raw.get("duration"))
                ),
                action=_optional_text(
                    (raw.get("navi") or {}).get("action")
                    if isinstance(raw.get("navi"), dict)
                    else None
                ),
                assistant_action=_optional_text(
                    (raw.get("navi") or {}).get("assistant_action")
                    if isinstance(raw.get("navi"), dict)
                    else None
                ),
                polyline=_bounded_polyline(raw.get("polyline")),
            )
        )
    return steps


def _transit_steps(raw_segments: list) -> list[RouteStepFact]:
    steps: list[RouteStepFact] = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        walking = segment.get("walking") if isinstance(segment.get("walking"), dict) else {}
        steps.extend(_ordinary_route_steps(walking.get("steps") or []))
        bus = segment.get("bus") if isinstance(segment.get("bus"), dict) else {}
        buslines = bus.get("buslines") or bus.get("steps") or []
        for line in buslines:
            if not isinstance(line, dict):
                continue
            departure = line.get("departure_stop") if isinstance(line.get("departure_stop"), dict) else {}
            arrival = line.get("arrival_stop") if isinstance(line.get("arrival_stop"), dict) else {}
            steps.append(
                RouteStepFact(
                    instruction=_optional_text(line.get("instruction")),
                    distance_km=_km(line.get("distance")),
                    duration_minutes=_minutes(_safe_float(line.get("duration"))),
                    transport=_optional_text(line.get("type")) or "公共交通",
                    departure_stop=_optional_text(departure.get("name")),
                    arrival_stop=_optional_text(arrival.get("name")),
                    line_name=_optional_text(line.get("name")),
                    first_time=_optional_text(line.get("start_time") or line.get("station_start_time")),
                    last_time=_optional_text(line.get("end_time") or line.get("station_end_time")),
                    polyline=_bounded_polyline(line.get("polyline")),
                )
            )
        railway = segment.get("railway") if isinstance(segment.get("railway"), dict) else {}
        if railway:
            departure = railway.get("departure_stop") if isinstance(railway.get("departure_stop"), dict) else {}
            arrival = railway.get("arrival_stop") if isinstance(railway.get("arrival_stop"), dict) else {}
            steps.append(
                RouteStepFact(
                    transport="铁路换乘",
                    departure_stop=_optional_text(departure.get("name")),
                    arrival_stop=_optional_text(arrival.get("name")),
                    line_name=_optional_text(railway.get("name") or railway.get("trip")),
                    distance_km=_km(railway.get("distance")),
                    duration_minutes=_minutes(_safe_float(railway.get("time"))),
                )
            )
    return steps


def _transit_transfer_count(item: dict) -> int:
    segments = item.get("segments") if isinstance(item.get("segments"), list) else []
    ride_count = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        bus = segment.get("bus") if isinstance(segment.get("bus"), dict) else {}
        rides = bus.get("buslines") or bus.get("steps") or []
        if isinstance(rides, list):
            ride_count += len(rides)
        if isinstance(segment.get("railway"), dict) and segment.get("railway"):
            ride_count += 1
    return max(0, ride_count - 1)


def _join_polylines(steps: list[RouteStepFact]) -> str | None:
    joined = ";".join(step.polyline for step in steps if step.polyline)
    return joined[:6000] or None


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
                    citycode=_optional_text(first.get("citycode")),
                    district=_optional_text(first.get("district")),
                    formatted_address=_optional_text(first.get("formatted_address")),
                )
    return _poi_location_detail(address, city=city_hint)


def reverse_geocode_detail(location: str) -> GeocodeResult | None:
    """Resolve coordinates to city/adcode metadata for weather and transit."""
    if not looks_like_coord(location):
        return None
    normalized = normalize_coord(location)
    data = _get(
        _REGEOCODE_PATH,
        {"location": normalized, "extensions": "base", "radius": 1000},
    )
    if not data:
        return None
    regeocode = data.get("regeocode") if isinstance(data.get("regeocode"), dict) else {}
    component = (
        regeocode.get("addressComponent")
        if isinstance(regeocode.get("addressComponent"), dict)
        else {}
    )
    city_value = _optional_text(component.get("city")) or _optional_text(
        component.get("province")
    )
    return GeocodeResult(
        location=normalized,
        adcode=_optional_text(component.get("adcode")),
        city=city_value,
        citycode=_optional_text(component.get("citycode")),
        district=_optional_text(component.get("district")),
        formatted_address=_optional_text(regeocode.get("formatted_address")),
    )


def resolve_city_adcode(city: str) -> str | None:
    """Resolve a model/user city label to the adcode required by weather."""
    data = _get(
        _DISTRICT_PATH,
        {"keywords": city, "subdistrict": 0, "extensions": "base"},
    )
    if data:
        districts = data.get("districts") or []
        if districts and isinstance(districts[0], dict):
            adcode = _optional_text(districts[0].get("adcode"))
            if adcode:
                return adcode
    detail = geocode_detail(f"{city}市" if not city.endswith("市") else city, city=city)
    return detail.adcode if detail else None


def poi_details(poi_ids: list[str]) -> list[PoiFact]:
    """Fetch rich details for up to ten selected POIs in one request."""
    cleaned = list(dict.fromkeys(value.strip() for value in poi_ids if value.strip()))[:10]
    if not cleaned:
        return []
    data = _get(
        _POI_DETAIL_PATH,
        {
            "id": "|".join(cleaned),
            "show_fields": "business,navi,photos",
        },
    )
    if data:
        pois = data.get("pois") or []
        if pois:
            return [
                _poi_fact_from_payload(item, fallback_name="POI")
                for item in pois[:10]
                if isinstance(item, dict)
            ]
    return [
        PoiFact(
            name=poi_id,
            address=None,
            location=None,
            category=None,
            status="unknown",
            poi_id=poi_id,
        )
        for poi_id in cleaned
    ]


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
    params: dict[str, str | int] = {
        "keywords": keyword,
        "page_size": 1,
        "page_num": 1,
    }
    if city:
        params["region"] = city
        params["city_limit"] = "true"
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
        citycode=_optional_text(first.get("citycode")),
        district=_optional_text(first.get("adname")),
        formatted_address=_optional_text(first.get("address")),
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


def _optional_text(value: object) -> str | None:
    if isinstance(value, list):
        return None
    text = str(value or "").strip()
    return text or None


def _optional_float(value: object) -> float | None:
    parsed = _safe_float(value)
    return parsed if parsed or str(value).strip() in {"0", "0.0"} else None


def _optional_int(value: object) -> int | None:
    parsed = _optional_float(value)
    return int(parsed) if parsed is not None else None


def _km(value: object) -> float | None:
    meters = _optional_float(value)
    return round(meters / 1000, 2) if meters is not None else None


def _minutes(seconds: float) -> int | None:
    return max(1, round(seconds / 60)) if seconds > 0 else None


def _bounded_polyline(value: object) -> str | None:
    text = _optional_text(value)
    return text[:3000] if text else None


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value.is_integer() else str(value)


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
