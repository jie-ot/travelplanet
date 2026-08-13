"""Deterministic itinerary structure classification.

Maps and itinerary posters both project from the same schedule kinds. Kinds
come from current Schedule fields and optional AMap POI types — never from
invented coordinates or a second model pass.
"""

from __future__ import annotations

from typing import Any, Literal

from app.models.itinerary import Schedule

ScheduleKind = Literal["hotel", "attraction", "dining", "transport", "shopping", "other"]

HOTEL_TAGS = frozenset({"住宿"})
DINING_TAGS = frozenset({"美食", "餐饮"})
TRANSPORT_TAGS = frozenset({"大交通", "换乘"})
SHOPPING_TAGS = frozenset({"购物"})
ATTRACTION_TAGS = frozenset(
    {
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
    }
)

_HOTEL_NAME = ("酒店", "宾馆", "客栈", "民宿", "旅馆", "度假村")
_HOTEL_ACTION = ("入住", "退房", "住宿")
_TRANSPORT_HUB = (
    "机场",
    "航站楼",
    "火车站",
    "高铁站",
    "客运站",
    "汽车站",
    "码头",
    "港口",
)
_WALKING_STREET = ("步行街", "古街", "老街", "商业街", "文化街", "美食街")
_DINING_NAME = ("餐厅", "饭店", "食馆", "小吃", "咖啡", "茶馆", "烧烤店")
_SHOPPING_NAME = ("商场", "购物中心", "免税店", "超市", "便利店")
_SCENIC_CATEGORY = (
    "风景名胜",
    "公园",
    "博物馆",
    "展览馆",
    "美术馆",
    "纪念馆",
    "科技馆",
    "天文馆",
    "寺庙",
    "古迹",
    "文物",
    "文化宫",
    "动物园",
    "植物园",
    "游乐园",
    "古镇",
    "景区",
)


def classify_schedule(
    schedule: Schedule, fact: dict[str, Any] | None = None
) -> ScheduleKind:
    """Return the structural kind of one schedule row."""
    if schedule.map_role in {"hotel", "attraction"}:
        return schedule.map_role

    tags = {tag.strip() for tag in schedule.tags if tag.strip()}
    text = _searchable(schedule)

    # Tags beat POI types and name heuristics. A hotel checkout whose activity
    # mentions 机场 must stay hotel; an airport fact_ref must not override 住宿.
    if tags & TRANSPORT_TAGS:
        return "transport"
    if tags & HOTEL_TAGS:
        return "hotel"
    if tags & ATTRACTION_TAGS or _is_walking_street(text):
        return "attraction"
    if tags & DINING_TAGS:
        return "dining"
    if tags & SHOPPING_TAGS:
        return "shopping"

    poi_kind = _kind_from_poi(fact, text)
    if poi_kind is not None:
        return poi_kind

    if _has_any(text, _TRANSPORT_HUB):
        return "transport"
    if _is_hotel_text(text):
        return "hotel"
    if _has_any(text, _DINING_NAME):
        return "dining"
    if _has_any(text, _SHOPPING_NAME):
        return "shopping"
    return "other"


def fact_for_schedule(
    schedule: Schedule, facts: dict[str, dict[str, Any]] | None
) -> dict[str, Any] | None:
    if not facts:
        return None
    for ref in schedule.fact_refs:
        fact = facts.get(ref)
        if isinstance(fact, dict):
            return fact
    return None


def _kind_from_poi(fact: dict[str, Any] | None, text: str) -> ScheduleKind | None:
    if not fact:
        return None
    typecode = str(fact.get("typecode") or "").strip()
    category = str(fact.get("category") or "")
    name = str(fact.get("name") or text)

    if typecode.startswith("15") or "交通设施" in category:
        return "transport"
    if typecode.startswith("10") or "住宿" in category:
        return "hotel"
    if typecode.startswith("11") or any(word in category for word in _SCENIC_CATEGORY):
        return "attraction"
    if typecode.startswith("14") and any(word in category for word in _SCENIC_CATEGORY):
        return "attraction"
    if _is_walking_street(name) or _is_walking_street(text):
        return "attraction"
    if typecode.startswith("05") or "餐饮" in category:
        return "dining"
    if typecode.startswith("06") or "购物" in category:
        return "shopping"
    return None


def _searchable(schedule: Schedule) -> str:
    return " ".join(
        part for part in (schedule.place_name, schedule.activity) if part
    )


def _is_hotel_text(text: str) -> bool:
    return _has_any(text, _HOTEL_NAME) and _has_any(text, _HOTEL_ACTION)


def _is_walking_street(text: str) -> bool:
    if _has_any(text, _WALKING_STREET):
        return True
    return "大街" in text and not _has_any(text, _DINING_NAME)


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
