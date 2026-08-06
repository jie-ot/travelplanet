"""Deterministic feasibility checks against the facts the model actually held.

The planning prompt forbids inventing flight times, route distances and travel
durations, but nothing enforced it: a plan could cite `fact_refs` while stating a
departure time no candidate offered, reuse one hotel's airport route for a
different hotel, or attach a `distance_km` that no route ever returned. These
checks compare the produced itinerary against the retained fact set so the
orchestrator can demand a targeted correction before the plan is returned.

Pure functions only — no I/O, no DB, no model calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.itinerary import ItineraryData, Schedule


@dataclass(frozen=True)
class Problem:
    """One deterministic finding.

    ``blocking`` marks a plan as untrustworthy — a departure time no flight offers,
    an invented train number, a cross-city leg backed by no schedule at all. Those
    must never reach the user unmarked. Non-blocking findings (a day that is too
    thin or too packed) still earn a repair round, but are not worth discarding a
    plan whose facts all check out.

    ``schedule_id`` lets a finding that survives every repair round be attached to
    the row it concerns, so the plan can ship with that row flagged instead of
    being thrown away whole.
    """

    message: str
    blocking: bool = True
    date: str | None = None
    schedule_id: str | None = None


FLIGHT_TOOLS = frozenset(
    {
        "searchFlightItineraries",
        "searchFlightsTransferinfo",
        "searchFlightandTrainTransferinfo",
    }
)
RAIL_TOOL = "query_rail_tickets"
ROUTE_TOOL = "amap_route"

MIN_SCHEDULES_PER_DAY = 3
MAX_SCHEDULES_PER_DAY = 6

_INTERCITY_MARKERS = ("航班", "飞机", "乘机", "高铁", "动车", "火车", "列车", "卧铺")
# 「高铁站」「火车站」 name a place you walk to, not a service you board, so a
# marker followed by 站 must not turn a transfer into a long-distance leg.
_INTERCITY_MARKER_PATTERN = re.compile(
    "(?:" + "|".join(_INTERCITY_MARKERS) + ")(?!站)"
)
_CITY_TRANSPORT_MODES = frozenset({"driving", "transit", "walking", "bicycling"})
# Flight numbers (CA4671, 3U5100, MF3433) and train numbers (G1234, D5, Y957)
# share a shape, so both pools are checked together. The boundaries are spelled
# out as "no adjacent ASCII alphanumeric" rather than \b, because Chinese
# characters are word characters too: \b never fires in 「乘HO2247航班」, which is
# exactly how plans write them, and the whole check silently saw nothing.
_TRANSPORT_CODE_PATTERN = re.compile(
    r"(?<![0-9A-Za-z])([0-9]?[A-Z]{1,2}[0-9]{2,5})(?![0-9A-Za-z])"
)
_CLOCK_PATTERN = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")
# 「G7 京新高速」「X12 县道」 are shaped exactly like 「G7 次列车」, so the road has to
# be told apart by what follows it rather than by the code itself.
_ROAD_SUFFIX_PATTERN = re.compile(r"\s*(?:高速|国道|省道|县道|公路|出口|匝道|收费站)")

# Tolerances: AMap route numbers are exact, so only rounding/leg-splitting slack
# is allowed before a value counts as invented.
_DISTANCE_TOLERANCE_RATIO = 0.30
_DISTANCE_TOLERANCE_MIN_KM = 1.5
_DURATION_TOLERANCE_RATIO = 0.40
_DURATION_TOLERANCE_MIN_MINUTES = 12


def _normalize_clock(value: str | None) -> str | None:
    if not value or not _CLOCK_PATTERN.match(value):
        return None
    hour, minute = value.split(":", 1)
    return f"{int(hour):02d}:{minute}"


def _schedule_text(schedule: Schedule) -> str:
    """What the row says it *does* — deliberately excluding `place_name`.

    Venue names are not statements about travel: a stay at 「丹东火车站鸭绿江断桥
    亚朵酒店」 once made "return to the hotel for lunch" read as an intercity rail
    leg, and the entire plan was rejected for citing no train.
    """
    return " ".join(
        part
        for part in (schedule.activity, schedule.transport, schedule.note)
        if part
    )


def _tool_of(fact: dict[str, Any]) -> str:
    return str(fact.get("tool") or "")


def _fact_time(fact: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = fact.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            # Flight facts carry "YYYY-MM-DD HH:MM"; rail facts carry "HH:MM".
            clock = text[-5:] if len(text) > 5 else text
            normalized = _normalize_clock(clock)
            if normalized:
                return normalized
    return None


def _transport_codes(fact: dict[str, Any]) -> set[str]:
    codes = {
        str(fact.get(key)).upper()
        for key in ("flight_no", "train_no")
        if fact.get(key)
    }
    siblings = fact.get("same_schedule_flight_nos")
    if isinstance(siblings, list):
        codes.update(str(item).upper() for item in siblings if item)
    return codes


def _is_transport_leg(
    schedule: Schedule,
    referenced: list[dict[str, Any]],
    transport_facts: list[dict[str, Any]],
) -> bool:
    """Is this row the long-distance service itself, rather than a transfer to it?

    Wording alone is not enough: the 2026-08-07 flash plan wrote
    「南京禄口机场乘坐 MU7708 出发前往大连」 with none of the marker words, and its
    11:30 start matched no flight. So a row also counts as the leg when it names a
    code its own citations returned, or when the only facts it leans on are the
    flight/rail schedules — in both cases the row is about that service, and its
    clock must come from it.
    """
    if schedule.transport_mode in _CITY_TRANSPORT_MODES:
        return False
    text = _schedule_text(schedule)
    if _INTERCITY_MARKER_PATTERN.search(text):
        return True
    if not transport_facts:
        return False
    upper = text.upper()
    cited_codes = {code for fact in transport_facts for code in _transport_codes(fact)}
    if any(code in upper for code in cited_codes):
        return True
    return len(transport_facts) == len(referenced)


def _within(actual: float, expected: float, ratio: float, floor: float) -> bool:
    return abs(actual - expected) <= max(floor, expected * ratio)


_UNBACKED_DISTANCE_NOTE = "路线距离与用时以地图实时导航为准"


def autofix(
    data: ItineraryData,
    retained_facts: dict[str, dict[str, Any]],
) -> tuple[ItineraryData, list[str]]:
    """Repair the violations that need no judgement, before asking the model.

    Dropping a stale `fact_ref` or clearing a `distance_km` that no route fact
    supports is a mechanical edit with exactly one correct outcome, so spending a
    model round on it only costs a minute and risks collateral rewrites. Anything
    requiring judgement (a departure time that matches no flight, an invented
    train number, a day that is too thin) is deliberately left to the model.

    Returns a repaired copy plus one description per applied fix.
    """
    facts = _usable_facts(retained_facts)
    repaired = data.model_copy(deep=True)
    applied: list[str] = []
    for day in repaired.itinerary:
        for schedule in day.schedules:
            label = f"{day.date} {schedule.id}"
            stale = [ref for ref in schedule.fact_refs if ref not in facts]
            if stale:
                schedule.fact_refs = [
                    ref for ref in schedule.fact_refs if ref in facts
                ]
                applied.append(f"{label} 移除了不存在的 fact_id {'、'.join(stale)}")
            applied.extend(
                _autofix_transport_times(label=label, schedule=schedule, facts=facts)
            )
            applied.extend(
                _autofix_route_numbers(label=label, schedule=schedule, facts=facts)
            )
    return repaired, applied


UNCONFIRMED_NOTE = "时刻待确认：本条与已查询班次不完全一致，出行前请按官方渠道核对"
# Traveller-facing copy only. Kept generic on purpose: the same strings must cover
# any city, any transport mode, and any day the gate cannot settle — never bake in
# a destination, a flight number, or the wording of one failed e2e run.
_DAY_ADVISORY = "{date} 的安排未通过自动核对，出行前请自行确认当天行程是否完整可执行"
_PLAN_ADVISORY = "部分预订信息与行程未完全对齐，出行前请以官方渠道查到的班次与时刻为准"


def annotate_unresolved(
    data: ItineraryData,
    problems: list[Problem],
) -> tuple[ItineraryData, list[str]]:
    """Mark what a repair round could not settle, so the plan can still ship.

    Discarding an otherwise good multi-city plan because one row's clock cannot be
    reconciled leaves the traveller with nothing; presenting that row as verified
    would be worse. So the row carries a visible caveat and drops to
    ``fact_status="unverified"``, and everything else stands.

    Findings attach at three scopes, covering every shape the gate can emit:

    - ``schedule_id`` set → annotate that row (wrong clock / invented code / …)
    - ``date`` set, no row → day-level advisory (thin day, missing evening leg, …)
    - neither → plan-level advisory (booking list vs itinerary mismatch, …)

    Without the latter two, any problem that is not about a single schedule would
    ship unmarked — which is how a plan can look fully verified while still
    contradicting the request. ``Problem.message`` stays model-facing repair
    instructions; advisories are rewritten for the traveller and never quote a
    specific code or city from the failing run.

    ``advisories`` is gate-owned: any value the model put in that field is cleared
    before we write, so a regenerated JSON cannot invent or wipe the caveats.

    Returns a copy plus one description per applied mark.
    """
    flagged: dict[tuple[str | None, str], Problem] = {}
    dates: list[str] = []
    plan_level = False
    for problem in problems:
        if problem.schedule_id:
            flagged.setdefault((problem.date, problem.schedule_id), problem)
        elif problem.date:
            if problem.date not in dates:
                dates.append(problem.date)
        else:
            plan_level = True
    if not flagged and not dates and not plan_level:
        return data, []
    annotated = data.model_copy(deep=True)
    annotated.advisories = []
    applied: list[str] = []
    for day in annotated.itinerary:
        for schedule in day.schedules:
            if (day.date, schedule.id) not in flagged:
                continue
            schedule.fact_status = "unverified"
            note = (schedule.note or "").strip()
            if UNCONFIRMED_NOTE not in note:
                schedule.note = f"{note}（{UNCONFIRMED_NOTE}）" if note else UNCONFIRMED_NOTE
            applied.append(f"{day.date} {schedule.id} 已标注为时刻待确认")
    for date in dates:
        advisory = _DAY_ADVISORY.format(date=date)
        annotated.advisories.append(advisory)
        applied.append(f"整单提醒：{advisory}")
    if plan_level:
        annotated.advisories.append(_PLAN_ADVISORY)
        applied.append(f"整单提醒：{_PLAN_ADVISORY}")
    return annotated, applied


def _autofix_transport_times(
    *,
    label: str,
    schedule: Schedule,
    facts: dict[str, dict[str, Any]],
) -> list[str]:
    """Snap a named service's clock to the timetable it cites.

    Only when the row names a code that its own citation returned, and that code
    has exactly one departure/arrival: then the intended service is unambiguous
    and there is one right answer. The 2026-08-07 flash plan wrote
    「乘 HO2247 航班飞往大连（07:10 起飞）」 starting at 05:00 because it folded
    check-in into the leg — rewriting 05:00 to 07:10 is bookkeeping, and losing a
    whole 15-minute plan over it is not a trade worth making. A row that merely
    cites a flight without naming it stays with the model, since it may well be
    the taxi to the airport rather than the flight.
    """
    named = _mentioned_codes(_schedule_text(schedule))
    if not named:
        return []
    matching = [
        facts[ref]
        for ref in schedule.fact_refs
        if ref in facts and _transport_codes(facts[ref]) & named
    ]
    if not matching:
        return []
    applied: list[str] = []
    for attribute, keys, noun in (
        ("start_time", ("depart_datetime", "depart_time"), "出发"),
        ("end_time", ("arrive_datetime", "arrive_time"), "到达"),
    ):
        current = _normalize_clock(getattr(schedule, attribute))
        candidates = {
            time for fact in matching if (time := _fact_time(fact, *keys)) is not None
        }
        if not current or len(candidates) != 1:
            continue
        expected = candidates.pop()
        if current != expected:
            setattr(schedule, attribute, expected)
            applied.append(
                f"{label} 的{noun}时间由 {current} 改为班次真实时刻 {expected}"
            )
    return applied


def _autofix_route_numbers(
    *,
    label: str,
    schedule: Schedule,
    facts: dict[str, dict[str, Any]],
) -> list[str]:
    """Align city-transfer distance/duration with the cited route fact."""
    if schedule.transport_mode not in _CITY_TRANSPORT_MODES:
        return []
    if not schedule.distance_km and not schedule.travel_minutes:
        return []
    route_facts = [
        facts[ref]
        for ref in schedule.fact_refs
        if ref in facts and _tool_of(facts[ref]) == ROUTE_TOOL
    ]
    if not route_facts:
        return [_clear_route_numbers(label, schedule, "没有引用任何 amap_route 事实")]
    if _route_endpoint_problems(
        label=label, schedule=schedule, route_facts=route_facts
    ):
        # The cited route belongs to another place, so its numbers say nothing
        # about this leg; keeping them would be the invented value we forbid.
        return [_clear_route_numbers(label, schedule, "所引路线事实的起终点与本条地点不符")]

    applied: list[str] = []
    distances = [
        float(fact["distance_km"])
        for fact in route_facts
        if isinstance(fact.get("distance_km"), (int, float))
    ]
    durations = [
        float(fact["duration_minutes"])
        for fact in route_facts
        if isinstance(fact.get("duration_minutes"), (int, float))
    ]
    if schedule.distance_km and distances:
        current = float(schedule.distance_km)
        if not any(
            _within(
                current, value, _DISTANCE_TOLERANCE_RATIO, _DISTANCE_TOLERANCE_MIN_KM
            )
            for value in distances
        ):
            # Snap to the nearest queried route rather than dropping the number:
            # the real value is right there in the fact.
            nearest = min(distances, key=lambda value: abs(value - current))
            schedule.distance_km = nearest
            applied.append(
                f"{label} 的 distance_km 由 {current:g} 改为路线事实的 {nearest:g}"
            )
    if schedule.travel_minutes and durations:
        current_minutes = float(schedule.travel_minutes)
        if not any(
            _within(
                current_minutes,
                value,
                _DURATION_TOLERANCE_RATIO,
                _DURATION_TOLERANCE_MIN_MINUTES,
            )
            for value in durations
        ):
            nearest = min(durations, key=lambda value: abs(value - current_minutes))
            schedule.travel_minutes = int(round(nearest))
            applied.append(
                f"{label} 的 travel_minutes 由 {current_minutes:g} 改为路线事实的 "
                f"{schedule.travel_minutes}"
            )
    return applied


def _clear_route_numbers(label: str, schedule: Schedule, reason: str) -> str:
    schedule.distance_km = None
    schedule.travel_minutes = None
    note = (schedule.note or "").strip()
    if _UNBACKED_DISTANCE_NOTE not in note:
        schedule.note = (
            f"{note}（{_UNBACKED_DISTANCE_NOTE}）" if note else _UNBACKED_DISTANCE_NOTE
        )
    return f"{label} {reason}，已清空 distance_km/travel_minutes 并注明以地图为准"


def _usable_facts(
    retained_facts: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    return {
        str(fact_id): fact
        for fact_id, fact in (retained_facts or {}).items()
        if isinstance(fact, dict)
    }


def find_problems(
    data: ItineraryData,
    retained_facts: dict[str, dict[str, Any]],
) -> list[str]:
    """Every problem the model should fix, most severe first."""
    return [problem.message for problem in find_problem_details(data, retained_facts)]


def find_problem_details(
    data: ItineraryData,
    retained_facts: dict[str, dict[str, Any]],
) -> list[Problem]:
    """Return structured problems so callers can weigh severity."""
    facts = _usable_facts(retained_facts)
    transport_code_pool = {
        code
        for fact in facts.values()
        if _tool_of(fact) in FLIGHT_TOOLS or _tool_of(fact) == RAIL_TOOL
        for code in _transport_codes(fact)
    }
    problems: list[Problem] = []
    for day in data.itinerary:
        problems.extend(_day_problems(day.date, day.schedules))
        for schedule in day.schedules:
            problems.extend(
                Problem(message=message, date=day.date, schedule_id=schedule.id)
                for message in _schedule_problems(
                    date=day.date,
                    schedule=schedule,
                    facts=facts,
                    transport_code_pool=transport_code_pool,
                )
            )
    problems.extend(
        Problem(message=message)
        for message in _booking_problems(
            data=data, transport_code_pool=transport_code_pool
        )
    )
    return problems


def _booking_problems(
    *,
    data: ItineraryData,
    transport_code_pool: set[str],
) -> list[str]:
    """Keep the booking list and the day plan telling the same story.

    The 2026-08-07 flash plan told the traveller to buy D7733 at 12:16 while the
    itinerary boarded D7717 at 18:13 — each defensible alone, together a missed
    train. Codes in bookings must therefore be real and must be the ones actually
    ridden.
    """
    itinerary_codes = {
        code
        for day in data.itinerary
        for schedule in day.schedules
        for code in _mentioned_codes(_schedule_text(schedule))
    }
    problems: list[str] = []
    for booking in data.bookings:
        for code in sorted(_mentioned_codes(booking.details)):
            if code not in transport_code_pool:
                problems.append(
                    f"预订清单里的 {code} 不是任何已查询事实返回的班次；"
                    "请改为真实查到的航班号/车次号，或删除具体班次"
                )
            elif code not in itinerary_codes:
                problems.append(
                    f"预订清单让用户购买 {code}，但没有任何日程乘坐它；"
                    "请让预订清单与当天行程使用同一班次"
                )
    return problems


def _mentioned_codes(text: str | None) -> set[str]:
    """Transport codes written in free text, minus road names and exit numbers."""
    if not text:
        return set()
    return {
        code
        for match in _TRANSPORT_CODE_PATTERN.finditer(text)
        if _looks_like_transport_code(code := match.group(1).upper())
        and not _ROAD_SUFFIX_PATTERN.match(text, match.end())
    }


def _day_problems(date: str, schedules: list[Schedule]) -> list[Problem]:
    if len(schedules) < MIN_SCHEDULES_PER_DAY:
        return [
            Problem(
                message=(
                    f"{date} 只有 {len(schedules)} 条日程，少于要求的 "
                    f"{MIN_SCHEDULES_PER_DAY} 条；请补足当天真实可执行的安排"
                ),
                blocking=False,
                date=date,
            )
        ]
    if len(schedules) > MAX_SCHEDULES_PER_DAY:
        return [
            Problem(
                message=(
                    f"{date} 有 {len(schedules)} 条日程，超过上限 "
                    f"{MAX_SCHEDULES_PER_DAY} 条；请把连续通勤与到访合并"
                ),
                blocking=False,
                date=date,
            )
        ]
    return []


def _schedule_problems(
    *,
    date: str,
    schedule: Schedule,
    facts: dict[str, dict[str, Any]],
    transport_code_pool: set[str],
) -> list[str]:
    problems: list[str] = []
    label = f"{date} {schedule.id}"
    referenced = [facts[ref] for ref in schedule.fact_refs if ref in facts]
    missing_refs = [ref for ref in schedule.fact_refs if ref not in facts]
    if missing_refs:
        problems.append(
            f"{label} 引用了不存在的 fact_id：{'、'.join(missing_refs[:3])}；"
            "只能引用保留事实中的 ID"
        )
    transport_facts = [
        fact
        for fact in referenced
        if _tool_of(fact) in FLIGHT_TOOLS or _tool_of(fact) == RAIL_TOOL
    ]
    route_facts = [fact for fact in referenced if _tool_of(fact) == ROUTE_TOOL]

    problems.extend(_clock_problems(label=label, schedule=schedule))
    problems.extend(
        _intercity_problems(
            label=label,
            schedule=schedule,
            referenced=referenced,
            transport_facts=transport_facts,
            transport_code_pool=transport_code_pool,
        )
    )
    problems.extend(
        _route_evidence_problems(
            label=label,
            schedule=schedule,
            route_facts=route_facts,
        )
    )
    return problems


def _clock_problems(*, label: str, schedule: Schedule) -> list[str]:
    """A schedule that starts and ends at the same minute cannot be executed."""
    start = _normalize_clock(schedule.start_time)
    end = _normalize_clock(schedule.end_time)
    if start and end and start == end:
        return [
            f"{label} 的开始与结束时间都是 {start}，时长为 0；"
            "请填写真实的起止时间（跨城交通取班次时刻，其余按实际停留时长）"
        ]
    return []


def _intercity_problems(
    *,
    label: str,
    schedule: Schedule,
    referenced: list[dict[str, Any]],
    transport_facts: list[dict[str, Any]],
    transport_code_pool: set[str],
) -> list[str]:
    problems: list[str] = []
    text = _schedule_text(schedule)
    intercity = _is_transport_leg(schedule, referenced, transport_facts)

    invented = sorted(
        code for code in _mentioned_codes(text) if code not in transport_code_pool
    )
    if invented:
        problems.append(
            f"{label} 写了未经查询的班次号 {'、'.join(invented[:3])}；"
            "只能使用保留事实中真实返回的航班号/车次号，否则不要写具体班次"
        )

    if not intercity:
        return problems

    if not transport_facts:
        problems.append(
            f"{label} 是跨城大交通，但没有引用任何航班/车次事实；"
            "请引用已查到的班次事实，或改写为不含具体时刻的待确认方案"
        )
        return problems

    allowed_departures = sorted(
        {
            time
            for fact in transport_facts
            if (time := _fact_time(fact, "depart_datetime", "depart_time")) is not None
        }
    )
    start = _normalize_clock(schedule.start_time)
    if start and allowed_departures and start not in allowed_departures:
        problems.append(
            f"{label} 的出发时间 {schedule.start_time} 不是所引用班次的真实发车/起飞时刻；"
            f"只能取其中之一：{'、'.join(allowed_departures)}"
        )

    allowed_arrivals = sorted(
        {
            time
            for fact in transport_facts
            if (time := _fact_time(fact, "arrive_datetime", "arrive_time")) is not None
        }
    )
    end = _normalize_clock(schedule.end_time)
    if end and allowed_arrivals and end not in allowed_arrivals:
        problems.append(
            f"{label} 的到达时间 {schedule.end_time} 不是所引用班次的真实到达时刻；"
            f"只能取其中之一：{'、'.join(allowed_arrivals)}"
        )
    return problems


def _looks_like_transport_code(code: str) -> bool:
    """Filter out incidental tokens such as 「G7」 road names or 「A1」 exits."""
    digits = sum(char.isdigit() for char in code)
    return digits >= 2 and len(code) >= 3


def _route_evidence_problems(
    *,
    label: str,
    schedule: Schedule,
    route_facts: list[dict[str, Any]],
) -> list[str]:
    problems: list[str] = []
    has_distance = bool(schedule.distance_km)
    has_travel = bool(schedule.travel_minutes)
    if not has_distance and not has_travel:
        return problems
    if schedule.transport_mode not in _CITY_TRANSPORT_MODES:
        return problems
    if not route_facts:
        problems.append(
            f"{label} 填了 distance_km/travel_minutes 但没有引用 amap_route 事实；"
            "请补引已查到的路线事实，或把这两个字段置为 null"
        )
        return problems

    if has_distance:
        expected = [
            float(fact["distance_km"])
            for fact in route_facts
            if isinstance(fact.get("distance_km"), (int, float))
        ]
        if expected and not any(
            _within(
                float(schedule.distance_km),
                value,
                _DISTANCE_TOLERANCE_RATIO,
                _DISTANCE_TOLERANCE_MIN_KM,
            )
            for value in expected
        ):
            problems.append(
                f"{label} 的 distance_km={schedule.distance_km} 与所引路线事实的 "
                f"{'、'.join(f'{value:g}' for value in expected)} 公里不符；"
                "请改为事实值或置为 null"
            )
    if has_travel:
        expected_minutes = [
            float(fact["duration_minutes"])
            for fact in route_facts
            if isinstance(fact.get("duration_minutes"), (int, float))
        ]
        if expected_minutes and not any(
            _within(
                float(schedule.travel_minutes),
                value,
                _DURATION_TOLERANCE_RATIO,
                _DURATION_TOLERANCE_MIN_MINUTES,
            )
            for value in expected_minutes
        ):
            problems.append(
                f"{label} 的 travel_minutes={schedule.travel_minutes} 与所引路线事实的 "
                f"{'、'.join(f'{value:g}' for value in expected_minutes)} 分钟不符；"
                "请改为事实值或置为 null"
            )

    problems.extend(
        _route_endpoint_problems(label=label, schedule=schedule, route_facts=route_facts)
    )
    return problems


def _route_endpoint_problems(
    *,
    label: str,
    schedule: Schedule,
    route_facts: list[dict[str, Any]],
) -> list[str]:
    """Catch a route fact reused for a place it never covered."""
    place = (schedule.place_name or "").strip()
    if len(place) < 3:
        return []
    for fact in route_facts:
        endpoints = [
            str(fact.get("origin") or ""),
            str(fact.get("destination") or ""),
        ]
        if any(
            endpoint and (endpoint in place or place in endpoint)
            for endpoint in endpoints
        ):
            return []
        if any(_shares_place_core(place, endpoint) for endpoint in endpoints):
            return []
    endpoint_text = "；".join(
        f"{fact.get('origin')}→{fact.get('destination')}" for fact in route_facts[:2]
    )
    return [
        f"{label} 的地点「{place}」不是所引路线事实的起终点（{endpoint_text}）；"
        "不得把一个地点的路线套用到另一个地点，请补查该地点的实际路线"
    ]


# A shared city prefix alone must not count as a match, so the overlap has to
# cover most of the shorter name.
_PLACE_OVERLAP_RATIO = 0.6
_PLACE_OVERLAP_MIN_CHARS = 4


def _shares_place_core(place: str, endpoint: str) -> bool:
    """Loose match so 「乌鲁木齐地窝堡国际机场」 still matches 「地窝堡机场」."""
    if not endpoint:
        return False
    cleaned_place = re.sub(r"[（）()\s]", "", place)
    cleaned_endpoint = re.sub(r"[（）()\s]", "", endpoint)
    shorter, longer = sorted((cleaned_place, cleaned_endpoint), key=len)
    if len(shorter) < _PLACE_OVERLAP_MIN_CHARS:
        return False
    overlap = _longest_common_substring_length(shorter, longer)
    return overlap >= max(
        _PLACE_OVERLAP_MIN_CHARS,
        int(len(shorter) * _PLACE_OVERLAP_RATIO),
    )


def _longest_common_substring_length(left: str, right: str) -> int:
    previous = [0] * (len(right) + 1)
    best = 0
    for left_index in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        for right_index in range(1, len(right) + 1):
            if left[left_index - 1] == right[right_index - 1]:
                current[right_index] = previous[right_index - 1] + 1
                best = max(best, current[right_index])
        previous = current
    return best
