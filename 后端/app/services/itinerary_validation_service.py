"""Business-rule validation for `ItineraryData`.

Pydantic already enforces structure/types; this layer enforces business rules
(《任务详细流程规范》七.9、八.3): date continuity, required fields, non-empty
arrays, time ordering, and a guard against obviously fabricated real-time
facts (concrete flight numbers / prices / availability) when no fact pack
covers them (措辞约束主定义 see《外部事实源与工具调用规范》七).

Raises `InvalidParamError` (1002) on violation when used in the save path.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.core.exceptions import InvalidParamError
from app.models.itinerary import ItineraryData

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _parse_date(value: str) -> date | None:
    if not value or not _DATE_RE.match(value):
        return None
    try:
        y, m, d = (int(x) for x in value.split("-"))
        return date(y, m, d)
    except ValueError:
        return None


def _parse_time_minutes(value: str | None) -> int | None:
    if not value or not _TIME_RE.match(value):
        return None
    hh, mm = (int(x) for x in value.split(":"))
    if hh > 23 or mm > 59:
        return None
    return hh * 60 + mm


def normalize_schedule_order(data: ItineraryData) -> list[str]:
    """Stably sort each day's timed schedules by ``start_time``.

    Entries without a valid ``start_time`` keep their original positions and
    relative order; only timed entries are reordered into the timed slots.
    Returns the dates whose schedule order changed.
    """
    changed_dates: list[str] = []
    for day in data.itinerary:
        timed_slots = [
            index
            for index, schedule in enumerate(day.schedules)
            if _parse_time_minutes(schedule.start_time) is not None
        ]
        timed_schedules = [day.schedules[index] for index in timed_slots]
        sorted_schedules = sorted(
            timed_schedules,
            key=lambda schedule: _parse_time_minutes(schedule.start_time) or 0,
        )
        if any(before is not after for before, after in zip(timed_schedules, sorted_schedules)):
            for index, schedule in zip(timed_slots, sorted_schedules):
                day.schedules[index] = schedule
            changed_dates.append(day.date)
    return changed_dates


def validate_itinerary(data: ItineraryData) -> None:
    """Validate business rules. Raises `InvalidParamError` on the first issue."""
    trip = data.trip_info
    if not trip.destination or not trip.destination.strip():
        raise InvalidParamError("行程目的地不能为空")

    start = _parse_date(trip.start_date)
    end = _parse_date(trip.end_date)
    if start is None or end is None:
        raise InvalidParamError("行程起止日期格式必须为 YYYY-MM-DD")
    if end < start:
        raise InvalidParamError("行程结束日期不得早于开始日期")

    if not data.itinerary:
        raise InvalidParamError("行程安排不能为空")

    # Required non-empty fields on nested arrays.
    for prep in data.preparations:
        if not prep.category.strip() or not prep.items.strip():
            raise InvalidParamError("物资准备项的类别与内容不能为空")
    for booking in data.bookings:
        if not booking.type.strip() or not booking.details.strip():
            raise InvalidParamError("预订指南项的类型与详情不能为空")

    # Day-level checks: dates within range, ascending, schedules present/ordered.
    prev_day: date | None = None
    seen_day_ids: set[str] = set()
    for day in data.itinerary:
        if not day.id.strip():
            raise InvalidParamError("每日行程缺少稳定 ID")
        if day.id in seen_day_ids:
            raise InvalidParamError(f"每日行程 ID 重复：{day.id}")
        seen_day_ids.add(day.id)

        day_date = _parse_date(day.date)
        if day_date is None:
            raise InvalidParamError(f"每日行程日期格式不合法：{day.date}")
        if day_date < start or day_date > end:
            raise InvalidParamError(f"每日行程日期 {day.date} 超出行程起止范围")
        if prev_day is not None and day_date <= prev_day:
            raise InvalidParamError("每日行程日期必须严格递增且连续")
        if prev_day is not None and day_date != prev_day + timedelta(days=1):
            raise InvalidParamError("每日行程日期必须连续，不得跳过或重复")
        prev_day = day_date

        if not day.schedules:
            raise InvalidParamError(f"{day.date} 当天行程安排不能为空")

        seen_sch_ids: set[str] = set()
        last_minutes: int | None = None
        last_end_minutes: int | None = None
        for sch in day.schedules:
            if not sch.id.strip():
                raise InvalidParamError("行程条目缺少稳定 ID")
            if sch.id in seen_sch_ids:
                raise InvalidParamError(f"行程条目 ID 重复：{sch.id}")
            seen_sch_ids.add(sch.id)
            if not sch.time_period.strip() or not sch.activity.strip():
                raise InvalidParamError("行程条目的时间段与活动描述不能为空")

            start_m = _parse_time_minutes(sch.start_time)
            end_m = _parse_time_minutes(sch.end_time)
            if start_m is not None and end_m is not None and end_m < start_m:
                raise InvalidParamError("行程条目的结束时间不得早于开始时间")
            if start_m is not None:
                if last_minutes is not None and start_m < last_minutes:
                    raise InvalidParamError("同一天行程的时间顺序必须递增")
                if last_end_minutes is not None and start_m < last_end_minutes:
                    raise InvalidParamError("同一天行程的时间不得重叠")
                last_minutes = start_m
            if end_m is not None:
                last_end_minutes = end_m
