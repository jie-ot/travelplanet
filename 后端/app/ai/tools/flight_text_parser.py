"""Structure the natural-language flight answers returned by VariFlight MCP.

The Aviation/Tripmatch MCP servers answer with a Chinese prose block instead of
a record list, so a generic "find the first list of dicts" walk finds nothing
and the concrete schedule silently disappears before the model ever reads it.
This module turns that prose into per-flight records that can each receive
their own ``fact_id``, and always keeps a bounded copy of the original text so a
future upstream format change degrades to "model sees the text" instead of
"model sees only status=ok".

Upstream shape (stable as of 2026-08):

    根据您的需求，我为您查询到了29条符合要求的航班，最低价:1200元，最短耗时:4h35m。
    最低价航班为： 航班号：UQ2578，起飞时间：2026-08-09 17:55:00，
      到达时间：2026-08-10 01:05:00，耗时：7h10m，无需中转，超值经济舱价格：1200元
    最短耗时航班为： 航班号：MF3433，...
    除了上述方案外还为您推荐以下方案：
    1. 航班号：GS7590，...
"""

from __future__ import annotations

import re
from typing import Any

# Cap the retained prose so one tool result cannot dominate the final prompt.
MAX_PROVIDER_TEXT_CHARS = 2000

_FLIGHT_ENTRY_PATTERN = re.compile(
    r"航班号[：:]\s*(?P<flight_no>[A-Z0-9]{2,8})"
    r"[^\n]*?起飞时间[：:]\s*(?P<depart>\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?)"
    r"[^\n]*?到达时间[：:]\s*(?P<arrive>\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?)"
    r"(?:[^\n]*?耗时[：:]\s*(?P<duration>[0-9hm ]+))?"
    r"(?P<tail>[^\n]*)"
)
_DURATION_PATTERN = re.compile(r"(?:(?P<hours>\d+)\s*h)?\s*(?:(?P<minutes>\d+)\s*m)?")
_PRICE_PATTERN = re.compile(r"(?P<price>\d+(?:\.\d+)?)\s*元")
_CABIN_PATTERN = re.compile(r"(?P<cabin>[\u4e00-\u9fff]{2,10}舱)\s*(?:价格)?[：:]?")
_REPORTED_TOTAL_PATTERN = re.compile(r"查询到了\s*(\d+)\s*条")
_LOWEST_PRICE_PATTERN = re.compile(r"最低价\s*[：:]\s*(\d+(?:\.\d+)?)\s*元")
_SHORTEST_DURATION_PATTERN = re.compile(r"最短耗时\s*[：:]\s*([0-9hm ]+)")

_ROLE_MARKERS: tuple[tuple[str, str], ...] = (
    ("最低价航班为", "lowest_price"),
    ("最短耗时航班为", "shortest_duration"),
    ("除了上述方案外还为您推荐", "recommended"),
    ("还为您推荐", "recommended"),
)


def _parse_duration_minutes(value: str | None) -> int | None:
    if not value:
        return None
    match = _DURATION_PATTERN.search(value.strip())
    if match is None:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    total = hours * 60 + minutes
    return total or None


def _normalize_datetime(value: str) -> str:
    """Drop upstream seconds so schedules compare against ``HH:MM`` cleanly."""
    date_part, _, time_part = value.strip().partition(" ")
    pieces = time_part.split(":")
    return f"{date_part} {pieces[0].zfill(2)}:{pieces[1]}"


def _role_for_offset(text: str, offset: int) -> str:
    """Classify one entry by the nearest preceding section marker."""
    best_role = "recommended"
    best_index = -1
    for marker, role in _ROLE_MARKERS:
        index = text.rfind(marker, 0, offset)
        if index > best_index:
            best_index = index
            best_role = role
    return best_role


def parse_flight_text(text: str | None) -> list[dict[str, Any]]:
    """Return one record per flight mentioned in an upstream prose answer."""
    if not text or "航班号" not in text:
        return []
    candidates: list[dict[str, Any]] = []
    for match in _FLIGHT_ENTRY_PATTERN.finditer(text):
        tail = match.group("tail") or ""
        price_match = _PRICE_PATTERN.search(tail)
        cabin_match = _CABIN_PATTERN.search(tail)
        depart = _normalize_datetime(match.group("depart"))
        arrive = _normalize_datetime(match.group("arrive"))
        candidates.append(
            {
                "flight_no": match.group("flight_no"),
                "depart_datetime": depart,
                "arrive_datetime": arrive,
                "depart_date": depart[:10],
                "depart_time": depart[11:],
                "arrive_date": arrive[:10],
                "arrive_time": arrive[11:],
                "arrives_next_day": arrive[:10] > depart[:10],
                "duration_text": (match.group("duration") or "").strip() or None,
                "duration_minutes": _parse_duration_minutes(match.group("duration")),
                "is_direct": "无需中转" in tail or "直飞" in tail,
                "transfer_note": "经过中转" if "经过中转" in tail else None,
                "cabin": cabin_match.group("cabin") if cabin_match else None,
                "price_cny": float(price_match.group("price")) if price_match else None,
                "selection_role": _role_for_offset(text, match.start()),
                "fact_status": "reference",
            }
        )
    _annotate_codeshare(candidates)
    return _dedupe_preserving_order(candidates)


def _annotate_codeshare(candidates: list[dict[str, Any]]) -> None:
    """Group identical schedules so the model can spot code-shared duplicates."""
    groups: dict[tuple[str, str], list[str]] = {}
    for item in candidates:
        key = (item["depart_datetime"], item["arrive_datetime"])
        groups.setdefault(key, []).append(item["flight_no"])
    for item in candidates:
        key = (item["depart_datetime"], item["arrive_datetime"])
        siblings = [no for no in groups[key] if no != item["flight_no"]]
        item["same_schedule_flight_nos"] = siblings
        item["likely_codeshare"] = bool(siblings)


def _dedupe_preserving_order(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the first mention of each flight number + schedule combination."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in candidates:
        key = (
            item["flight_no"],
            item["depart_datetime"],
            item["arrive_datetime"],
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def parse_flight_summary(text: str | None) -> dict[str, Any]:
    """Extract the header aggregates that the per-flight list does not carry."""
    if not text:
        return {}
    summary: dict[str, Any] = {}
    total = _REPORTED_TOTAL_PATTERN.search(text)
    if total:
        summary["upstream_reported_flight_count"] = int(total.group(1))
    lowest = _LOWEST_PRICE_PATTERN.search(text)
    if lowest:
        summary["upstream_lowest_price_cny"] = float(lowest.group(1))
    shortest = _SHORTEST_DURATION_PATTERN.search(text)
    if shortest:
        summary["upstream_shortest_duration_text"] = shortest.group(1).strip()
        summary["upstream_shortest_duration_minutes"] = _parse_duration_minutes(
            shortest.group(1)
        )
    return summary


def provider_text(payload: Any, raw_text: str | None) -> str | None:
    """Pick the most informative upstream text without exceeding the cap."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), str):
        chosen = payload["data"]
    elif isinstance(payload, str):
        chosen = payload
    else:
        chosen = raw_text or ""
    chosen = chosen.strip()
    if not chosen:
        return None
    if len(chosen) <= MAX_PROVIDER_TEXT_CHARS:
        return chosen
    return chosen[:MAX_PROVIDER_TEXT_CHARS] + "…（上游原文已截断）"


def departure_window(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the real departure span so the model cannot invent one."""
    times = sorted(
        item["depart_datetime"] for item in candidates if item.get("depart_datetime")
    )
    if not times:
        return {}
    return {
        "earliest_departure": times[0],
        "latest_departure": times[-1],
        "available_departure_datetimes": times,
        "constraint": (
            "行程中该航段的出发时刻必须等于以上某一个已查到的起飞时刻，"
            "不得自拟时间；抵达时刻同样必须取自同一候选。"
        ),
    }
