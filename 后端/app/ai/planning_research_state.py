"""Deterministic research-state repairs that do not wait on the model.

The model may prune too early, leave stale remainingQueries, or omit POI
coordinates in the final JSON. These helpers keep newly fetched facts, drop
queries the registry already covers, and restore whole tool-queries that were
never selected — without resurrecting sibling candidates the model discarded.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.tools import tool_specs

_POI_TOOLS = frozenset(
    {
        tool_specs.TOOL_AMAP_POI_SEARCH,
        tool_specs.TOOL_AMAP_POI_AROUND,
        tool_specs.TOOL_AMAP_POI_DETAIL,
    }
)
_FLIGHT_TOOLS = frozenset(
    {
        tool_specs.TOOL_SEARCH_FLIGHT_ITINERARIES,
        tool_specs.TOOL_SEARCH_FLIGHT_TRANSFER,
        tool_specs.TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER,
    }
)
_LIST_SPLIT = re.compile(r"[、,/|；;]+")
_PAREN_LIST = re.compile(r"[（(]([^）)]+)[）)]")
_RETURN_MARKERS = ("返程", "返回", "回程")
_DINING_MARKERS = ("餐饮", "餐厅", "美食", "配套")
_NEARBY_MARKERS = ("周边", "附近", "旁边")
_ROUTE_MARKERS = ("路线", "接驳", "怎么去", "耗时")
_FLIGHT_MARKERS = ("航班", "飞机", "机票")
_RAIL_MARKERS = ("铁路", "火车", "高铁", "车次")
_HOTEL_MARKERS = ("酒店", "住宿", "民宿", "客栈")
_MISSING_POI_PREFIX = "仍缺景点POI："


def query_cache_key(tool_name: str, arguments: dict[str, Any] | None) -> str:
    return f"{tool_name}:{json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)}"


def merge_new_turn_facts(
    retained_facts: dict[str, dict[str, Any]],
    fact_registry: dict[str, dict[str, Any]],
    fact_ids_at_turn_start: set[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Keep facts issued this turn even if a same-turn prune could not name them."""
    merged = dict(retained_facts)
    added: list[str] = []
    for fact_id, fact in fact_registry.items():
        if fact_id in fact_ids_at_turn_start or fact_id in merged:
            continue
        merged[fact_id] = fact
        added.append(fact_id)
    return merged, added


def union_unrepresented_query_facts(
    retained_facts: dict[str, dict[str, Any]],
    fact_registry: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Add every fact from tool-queries that retained_facts never selected.

    Sibling candidates from an already-represented query stay dropped.
    """
    merged = dict(retained_facts)
    represented = {
        query_cache_key(
            str(fact.get("tool") or ""),
            fact.get("arguments") if isinstance(fact.get("arguments"), dict) else {},
        )
        for fact in retained_facts.values()
    }
    added: list[str] = []
    for fact_id, fact in fact_registry.items():
        if fact_id in merged:
            continue
        key = query_cache_key(
            str(fact.get("tool") or ""),
            fact.get("arguments") if isinstance(fact.get("arguments"), dict) else {},
        )
        if key in represented:
            continue
        merged[fact_id] = fact
        added.append(fact_id)
    return merged, added


def listed_place_names(query: str) -> list[str]:
    text = query or ""
    match = _PAREN_LIST.search(text)
    blob = match.group(1) if match else ""
    if not blob and text.startswith(_MISSING_POI_PREFIX):
        blob = text[len(_MISSING_POI_PREFIX) :]
    if not blob:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for part in _LIST_SPLIT.split(blob):
        name = part.strip()
        if len(name) < 2 or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def has_poi_named(name: str, fact_registry: dict[str, dict[str, Any]]) -> bool:
    needle = (name or "").strip()
    if len(needle) < 2:
        return False
    for fact in fact_registry.values():
        if str(fact.get("tool") or "") not in _POI_TOOLS:
            continue
        fact_name = str(fact.get("name") or "").strip()
        if needle == fact_name or needle in fact_name:
            return True
    return False


def remaining_query_is_covered(
    query: str,
    *,
    transport_query_count: int,
    destination_count: int,
    has_flight: bool,
    has_rail: bool,
    has_route: bool,
    has_poi_around: bool,
    has_poi_search: bool,
    has_hotel: bool,
    fact_registry: dict[str, dict[str, Any]],
) -> bool:
    """True only when the registry clearly already answers this leftover query."""
    text = (query or "").strip()
    if not text:
        return True
    listed = listed_place_names(text)
    if listed:
        return all(has_poi_named(name, fact_registry) for name in listed)

    if any(marker in text for marker in _RETURN_MARKERS):
        needed = max(1, destination_count + 1)
        return transport_query_count >= needed and (has_flight or has_rail)
    if any(marker in text for marker in _DINING_MARKERS) and (
        any(marker in text for marker in _NEARBY_MARKERS)
        or any(marker in text for marker in _HOTEL_MARKERS)
    ):
        return has_poi_around or has_poi_search
    if any(marker in text for marker in _ROUTE_MARKERS):
        return has_route
    if any(marker in text for marker in _FLIGHT_MARKERS):
        return has_flight
    if any(marker in text for marker in _RAIL_MARKERS):
        return has_rail
    if any(marker in text for marker in _HOTEL_MARKERS):
        return has_hotel
    return False


def refine_remaining_queries(
    remaining_queries: list[str],
    *,
    transport_query_count: int,
    destination_count: int,
    has_flight: bool,
    has_rail: bool,
    has_route: bool,
    has_poi_around: bool,
    has_poi_search: bool,
    has_hotel: bool,
    fact_registry: dict[str, dict[str, Any]],
) -> list[str]:
    """Drop or shrink leftover queries that the registry already answers."""
    refined: list[str] = []
    seen: set[str] = set()
    cover_kwargs = {
        "transport_query_count": transport_query_count,
        "destination_count": destination_count,
        "has_flight": has_flight,
        "has_rail": has_rail,
        "has_route": has_route,
        "has_poi_around": has_poi_around,
        "has_poi_search": has_poi_search,
        "has_hotel": has_hotel,
        "fact_registry": fact_registry,
    }
    for query in remaining_queries:
        text = (query or "").strip()
        if not text:
            continue
        listed = listed_place_names(text)
        if listed:
            missing = [name for name in listed if not has_poi_named(name, fact_registry)]
            if not missing:
                continue
            if len(missing) < len(listed):
                text = "仍缺景点POI：" + "/".join(missing)
            # else keep the original wording; nothing in the list was found
        elif remaining_query_is_covered(text, **cover_kwargs):
            continue
        if text in seen:
            continue
        seen.add(text)
        refined.append(text)
    return refined
