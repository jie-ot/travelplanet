"""Build cached AMap static play maps from DeepSeek schedule annotations."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Literal

from app.ai.model_selection import PlanningModel, supports_daily_map_planning
from app.ai.tools import amap_provider
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


@dataclass(frozen=True)
class _Candidate:
    schedule: Schedule
    location: str
    group: str
    name: str
    kind: Literal["hotel", "attraction"]


def enrich_daily_maps(data: ItineraryData, planning_model: PlanningModel) -> ItineraryData:
    """Populate optional daily maps without making itinerary generation fail."""
    for day in data.itinerary:
        day.daily_maps = []
    if not supports_daily_map_planning(planning_model):
        return data

    pending: list[tuple[DailyItinerary, DailyMap, dict[str, str | int]]] = []
    for day in data.itinerary:
        groups = _candidate_groups(day)
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


def _candidate_groups(day: DailyItinerary) -> OrderedDict[str, list[_Candidate]]:
    groups: OrderedDict[str, list[_Candidate]] = OrderedDict()
    has_model_annotations = any(
        schedule.map_role or schedule.map_group or schedule.map_label
        for schedule in day.schedules
    )
    for schedule in day.schedules:
        candidate = _candidate(
            schedule,
            fallback_group=None if has_model_annotations else _fallback_group(day),
        )
        if candidate is None:
            continue
        groups.setdefault(candidate.group, []).append(candidate)

    valid: OrderedDict[str, list[_Candidate]] = OrderedDict()
    for group, candidates in groups.items():
        deduped: list[_Candidate] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            key = (candidate.location, candidate.name)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        if len(deduped) >= 2:
            valid[group] = deduped[:_MAX_POINTS_PER_MAP]
        if len(valid) >= _MAX_MAPS_PER_DAY:
            break
    return valid


def _candidate(schedule: Schedule, *, fallback_group: str | None = None) -> _Candidate | None:
    role = schedule.map_role
    group_hint = schedule.map_group
    label_hint = schedule.map_label
    if fallback_group and not (role or group_hint or label_hint):
        inferred = _fallback_semantics(schedule, fallback_group)
        if inferred:
            role, group_hint, label_hint = inferred
    if (
        role not in {"hotel", "attraction"}
        or not group_hint
        or not schedule.location
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
    group = " ".join(group_hint.split())[:24]
    name = " ".join(
        (label_hint or schedule.place_name or schedule.activity).split()
    )[:32]
    if not group or not name:
        return None
    return _Candidate(
        schedule=schedule,
        location=location,
        group=group,
        name=name,
        kind=role,
    )


def _fallback_group(day: DailyItinerary) -> str:
    """Provide a conservative group only when DeepSeek omitted every map hint."""
    prefix = (day.title or "").split("：", 1)[0].strip()
    prefix = re.split(r"[—→>-]", prefix)[-1].strip()
    return (prefix or "当日游玩")[:24]


def _fallback_semantics(
    schedule: Schedule, group: str
) -> tuple[str, str, str] | None:
    """Recover reliable maps from verified schedule tags without another model call."""
    tags = {tag.strip() for tag in schedule.tags if tag.strip()}
    searchable = " ".join(
        part for part in (schedule.place_name, schedule.activity) if part
    )
    name = (schedule.place_name or schedule.activity).strip()
    if "住宿" in tags and re.search(r"(酒店|客栈|民宿|旅馆|入住|退房)", searchable):
        return "hotel", group, name
    if tags & {"大交通", "换乘", "美食", "餐饮", "购物"}:
        return None
    attraction_markers = (
        "历史",
        "文化",
        "景区",
        "风景",
        "自然",
        "滨海",
        "博物馆",
        "公园",
        "广场",
        "建筑",
        "红色",
        "演出",
        "古镇",
        "街区",
    )
    if any(marker in tag for tag in tags for marker in attraction_markers):
        return "attraction", group, name
    return None


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
