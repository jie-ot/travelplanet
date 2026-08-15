"""Build cached AMap static play maps by projecting itinerary structure."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Any, Literal

from app.ai.model_selection import PlanningModel, supports_daily_map_planning
from app.ai.tools import amap_provider, tool_specs
from app.core.business_logging import log_event
from app.models.itinerary import (
    DailyItinerary,
    DailyMap,
    DailyMapLeg,
    DailyMapPoint,
    ItineraryData,
    Schedule,
)
from app.services import storage_service
from app.services.schedule_kind import classify_schedule, fact_for_schedule

_MARKERS = "ABCDEFGHIJ"
_MAX_MAPS_PER_DAY = 3
_MAX_POINTS_PER_MAP = 10
_MAP_SIZE = "500*300"
_MAP_SCALE = 2
_TRANSPORT_LABELS = {
    "walking": "步行",
    "transit": "公交地铁",
    "driving": "打车",
    "bicycling": "骑行",
}
_TRANSPORT_TEXT_PATTERN = re.compile(r"(步行|地铁|公交|打车|出租车|网约车|骑行)")
_CITY_SPLIT = re.compile(r"[、,，/—–→>-]")


@dataclass(frozen=True)
class _Candidate:
    schedule: Schedule
    location: str
    group: str
    name: str
    kind: Literal["hotel", "attraction"]


def enrich_daily_maps(
    data: ItineraryData,
    planning_model: PlanningModel,
    *,
    facts: dict[str, dict[str, Any]] | None = None,
) -> ItineraryData:
    """Populate optional daily maps without making itinerary generation fail."""
    for day in data.itinerary:
        day.daily_maps = []
    if not supports_daily_map_planning(planning_model):
        return data

    pending: list[tuple[DailyItinerary, DailyMap, dict[str, str | int]]] = []
    overnight_hotel: _Candidate | None = None
    overnight_city = _trip_city(data.trip_info.destination)
    for day in data.itinerary:
        groups, overnight_hotel, overnight_city = _candidate_groups(
            day,
            data,
            facts=facts,
            overnight_hotel=overnight_hotel,
            overnight_city=overnight_city,
        )
        for group_index, (group_name, candidates) in enumerate(groups.items()):
            daily_map, params = _build_map(day, group_name, group_index, candidates)
            day.daily_maps.append(daily_map)
            pending.append((day, daily_map, params))

    if pending:
        with ThreadPoolExecutor(max_workers=min(3, len(pending))) as pool:
            results = list(pool.map(lambda item: _render_cached_map(item[2]), pending))
        for (day, daily_map, _), image_url in zip(pending, results):
            if image_url:
                daily_map.image_url = image_url
                daily_map.status = "ready"
            log_event(
                "planning_daily_map",
                status=daily_map.status,
                date=day.date,
                map_title=daily_map.title,
                point_count=len(daily_map.points),
                image_url=image_url,
                planning_model=planning_model,
            )
    return data


_POI_BIND_TOOLS = frozenset(
    {
        tool_specs.TOOL_AMAP_POI_SEARCH,
        tool_specs.TOOL_AMAP_POI_AROUND,
        tool_specs.TOOL_AMAP_POI_DETAIL,
    }
)


def bind_verified_poi_locations(
    data: ItineraryData,
    facts: dict[str, dict[str, Any]] | None,
) -> ItineraryData:
    """Fill missing schedule coordinates from exact-name retained POI facts.

    Does not invent names, overwrite trusted coordinates, or touch reference
    transport rows. Same-name POIs with conflicting coordinates are skipped.
    """
    if not facts:
        return data
    index = _exact_poi_coord_index(facts)
    if not index:
        return data
    updated = data.model_copy(deep=True)
    bound = 0
    for day in updated.itinerary:
        for schedule in day.schedules:
            if schedule.fact_status == "reference":
                continue
            if classify_schedule(schedule, fact_for_schedule(schedule, facts)) == "transport":
                continue
            if _trusted_coord(schedule):
                continue
            place = " ".join((schedule.place_name or "").split())
            matched = index.get(place)
            if matched is None:
                continue
            fact_id, location = matched
            schedule.location = location
            if fact_id not in schedule.fact_refs:
                schedule.fact_refs = [*schedule.fact_refs, fact_id]
            schedule.fact_status = "verified"
            bound += 1
    if bound:
        log_event(
            "planning_poi_location_bound",
            status="success",
            bound_count=bound,
        )
    return updated


def _exact_poi_coord_index(
    facts: dict[str, dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for fact_id, fact in facts.items():
        if str(fact.get("tool") or "") not in _POI_BIND_TOOLS:
            continue
        status = str(fact.get("status") or "").lower()
        context = fact.get("result_context")
        context_status = (
            str(context.get("status") or "").lower()
            if isinstance(context, dict)
            else ""
        )
        if status != "ok" and context_status != "ok":
            continue
        name = " ".join(str(fact.get("name") or "").split())
        location = _fact_map_coord(fact)
        if not name or not location:
            continue
        grouped.setdefault(name, []).append((fact_id, location))
    index: dict[str, tuple[str, str]] = {}
    for name, items in grouped.items():
        coords = {coord for _, coord in items}
        if len(coords) != 1:
            continue
        index[name] = items[0]
    return index


def _fact_map_coord(fact: dict[str, Any]) -> str | None:
    raw = str(fact.get("location") or "").strip()
    if not raw or not amap_provider.looks_like_coord(raw):
        return None
    location = amap_provider.normalize_coord(raw)
    try:
        lng_text, lat_text = location.split(",", 1)
        lng, lat = float(lng_text), float(lat_text)
    except (TypeError, ValueError):
        return None
    if not (73 <= lng <= 136 and 3 <= lat <= 54):
        return None
    return location


def _candidate_groups(
    day: DailyItinerary,
    data: ItineraryData,
    *,
    facts: dict[str, dict[str, Any]] | None,
    overnight_hotel: _Candidate | None,
    overnight_city: str,
) -> tuple[OrderedDict[str, list[_Candidate]], _Candidate | None, str]:
    hotels_today: list[_Candidate] = []
    attractions: list[_Candidate] = []
    checkin: _Candidate | None = None
    checkout: _Candidate | None = None
    for schedule in day.schedules:
        fact = fact_for_schedule(schedule, facts)
        kind = classify_schedule(schedule, fact)
        city = _point_city(schedule, fact, overnight_city, data.trip_info.destination)
        candidate = _map_candidate(schedule, kind=kind, city=city)
        if candidate is None:
            continue
        if candidate.kind == "hotel":
            hotels_today.append(candidate)
            searchable = " ".join(
                part for part in (schedule.place_name, schedule.activity) if part
            )
            if "入住" in searchable:
                checkin = candidate
            elif "退房" in searchable:
                checkout = candidate
            elif checkin is None:
                checkin = candidate
        elif candidate.kind == "attraction":
            attractions.append(candidate)

    if not attractions:
        next_hotel = checkin or (None if checkout else overnight_hotel)
        next_city = next_hotel.group if next_hotel else overnight_city
        return OrderedDict(), next_hotel, next_city

    points: list[_Candidate] = []
    seen: set[tuple[str, str]] = set()

    def add(candidate: _Candidate | None) -> None:
        if candidate is None:
            return
        key = (candidate.location, candidate.name)
        if key in seen:
            return
        seen.add(key)
        points.append(candidate)

    carried = overnight_hotel
    if checkout and carried and carried.location == checkout.location:
        carried = None
    add(carried)
    for hotel in hotels_today:
        add(hotel)
    for attraction in attractions:
        add(attraction)

    groups: OrderedDict[str, list[_Candidate]] = OrderedDict()
    for candidate in points:
        groups.setdefault(candidate.group, []).append(candidate)

    valid: OrderedDict[str, list[_Candidate]] = OrderedDict()
    for group, candidates in groups.items():
        if len(candidates) >= 2:
            valid[group] = candidates[:_MAX_POINTS_PER_MAP]
        if len(valid) >= _MAX_MAPS_PER_DAY:
            break

    next_hotel = checkin or (None if checkout else overnight_hotel)
    next_city = next_hotel.group if next_hotel else overnight_city
    return valid, next_hotel, next_city


def _map_candidate(
    schedule: Schedule,
    *,
    kind: str,
    city: str,
) -> _Candidate | None:
    if kind not in {"hotel", "attraction"}:
        return None
    location = _trusted_coord(schedule)
    if location is None:
        return None
    name = " ".join(
        (schedule.map_label or schedule.place_name or schedule.activity).split()
    )[:32]
    group = " ".join(city.split())[:24]
    if not name or not group:
        return None
    return _Candidate(
        schedule=schedule,
        location=location,
        group=group,
        name=name,
        kind=kind,
    )


def _trusted_coord(schedule: Schedule) -> str | None:
    if (
        not schedule.location
        or schedule.fact_status != "verified"
        or not schedule.fact_refs
        or not amap_provider.looks_like_coord(schedule.location)
    ):
        return None
    location = amap_provider.normalize_coord(schedule.location)
    try:
        lng_text, lat_text = location.split(",", 1)
        lng, lat = float(lng_text), float(lat_text)
    except (TypeError, ValueError):
        return None
    if not (73 <= lng <= 136 and 3 <= lat <= 54):
        return None
    return location


def _point_city(
    schedule: Schedule,
    fact: dict[str, Any] | None,
    overnight_city: str,
    destination: str,
) -> str:
    if fact:
        for key in ("city_name", "city"):
            value = str(fact.get(key) or "").strip()
            if value:
                return re.sub(r"(市)$", "", value)[:24]
    text = " ".join(
        part for part in (schedule.place_name, schedule.activity) if part
    )
    for city in _trip_cities(destination):
        if city and city in text:
            return city[:24]
    hinted = amap_provider.infer_city_hint(schedule.place_name, schedule.activity)
    if hinted:
        return hinted[:24]
    if overnight_city:
        return overnight_city[:24]
    return _trip_city(destination)


def _trip_cities(destination: str) -> list[str]:
    parts = [
        re.sub(r"(市)$", "", part.strip())
        for part in _CITY_SPLIT.split(destination or "")
        if part.strip()
    ]
    return [part for part in parts if part]


def _trip_city(destination: str) -> str:
    cities = _trip_cities(destination)
    return (cities[0] if cities else "当日游玩")[:24]


def _build_map(
    day: DailyItinerary,
    group_name: str,
    group_index: int,
    candidates: list[_Candidate],
) -> tuple[DailyMap, dict[str, str | int]]:
    points = [
        DailyMapPoint(
            schedule_id=candidate.schedule.id,
            marker=_MARKERS[index],
            name=candidate.name,
            location=candidate.location,
            kind=candidate.kind,
        )
        for index, candidate in enumerate(candidates)
    ]
    legs = [
        DailyMapLeg(
            origin_marker=points[index - 1].marker,
            destination_marker=points[index].marker,
            transport_text=_transport_text(candidates[index].schedule),
        )
        for index in range(1, len(points))
    ]
    map_id = f"map_{day.date.replace('-', '')}_{group_index + 1}"
    daily_map = DailyMap(
        id=map_id,
        title=group_name,
        points=points,
        legs=legs,
    )
    marker_parts = []
    for point in points:
        color = "0x2D7FA3" if point.kind == "hotel" else "0xE06A4E"
        marker_parts.append(f"mid,{color},{point.marker}:{point.location}")
    params: dict[str, str | int] = {
        "size": _MAP_SIZE,
        "scale": _MAP_SCALE,
        "traffic": 0,
        "markers": "|".join(marker_parts),
        "paths": "4,0x2D7FA3,0.72,,:" + ";".join(point.location for point in points),
    }
    return daily_map, params


def _transport_text(schedule: Schedule) -> str:
    label = _TRANSPORT_LABELS.get(schedule.transport_mode or "")
    if not label and schedule.transport:
        match = _TRANSPORT_TEXT_PATTERN.search(schedule.transport)
        if match:
            label = "打车" if match.group(1) in {"出租车", "网约车"} else match.group(1)
    label = label or "按行程交通"
    return f"{label}{schedule.travel_minutes}分钟" if schedule.travel_minutes else label


def _render_cached_map(params: dict[str, str | int]) -> str | None:
    digest = hashlib.sha256(
        json.dumps(params, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    relative_path = f"/static/images/daily-maps/{digest}.png"
    absolute_path = storage_service.resolve_static_path(relative_path)
    if os.path.isfile(absolute_path) and os.path.getsize(absolute_path) > 0:
        return relative_path
    image = amap_provider.static_map_image(params)
    if not image:
        return None
    storage_service.save_bytes_to_static(image, relative_path, mime_type="image/png")
    return relative_path
