"""Stable ID alignment for `ItineraryData`.

Stable-ID铁律 master definition: 《数据结构与通信接口规范》1.9. Unmodified
day/schedule items must keep their original IDs; only newly added items get new
IDs. Never trust the model with ID stability — the backend re-aligns by
position/content diff against the previous `context` before returning.

Used by #6 `/api/ai/planning` after model output is parsed, and re-validated at
save time.
"""

from __future__ import annotations

from app.models.itinerary import DailyItinerary, ItineraryData, Schedule
from app.services import id_service


def _valid_day_id(value: str | None) -> bool:
    return bool(value) and value.startswith(id_service.PREFIX_DAY)


def _valid_schedule_id(value: str | None) -> bool:
    return bool(value) and value.startswith(id_service.PREFIX_SCHEDULE)


def _align_schedules(
    new_schedules: list[Schedule], old_schedules: list[Schedule]
) -> list[Schedule]:
    """Map old schedule IDs onto new schedules by content, then by position."""
    used_old_ids: set[str] = set()
    # First pass: exact content match (activity + time_period) reuses old ID.
    old_by_signature: dict[tuple[str, str], list[Schedule]] = {}
    for old in old_schedules:
        old_by_signature.setdefault((old.time_period, old.activity), []).append(old)

    result: list[Schedule] = []
    unmatched_indexes: list[int] = []
    for idx, sch in enumerate(new_schedules):
        sig = (sch.time_period, sch.activity)
        candidates = old_by_signature.get(sig)
        matched = None
        if candidates:
            for cand in candidates:
                if cand.id not in used_old_ids:
                    matched = cand
                    break
        if matched is not None:
            used_old_ids.add(matched.id)
            sch.id = matched.id
            result.append(sch)
        else:
            unmatched_indexes.append(idx)
            result.append(sch)

    # Second pass: positional reuse for remaining old IDs (edits in place).
    remaining_old = [o for o in old_schedules if o.id not in used_old_ids]
    for idx in unmatched_indexes:
        sch = result[idx]
        if remaining_old:
            reuse = remaining_old.pop(0)
            used_old_ids.add(reuse.id)
            sch.id = reuse.id
        elif not _valid_schedule_id(sch.id) or sch.id in used_old_ids:
            sch.id = id_service.new_schedule_id()
            used_old_ids.add(sch.id)
        else:
            # A model-provided ID is only safe when no exact/positional match or
            # earlier unmatched entry in this day has already claimed it.
            used_old_ids.add(sch.id)
    # Ensure any still-invalid IDs get fresh ones.
    for sch in result:
        if not _valid_schedule_id(sch.id):
            sch.id = id_service.new_schedule_id()
    return result


def align_ids(new_data: ItineraryData, context: ItineraryData | None) -> ItineraryData:
    """Align IDs of `new_data` against `context` (the previous itinerary).

    First turn (`context is None`): assign deterministic day IDs by date and
    fresh schedule IDs where missing/invalid.
    """
    if context is None:
        for day in new_data.itinerary:
            if not _valid_day_id(day.id):
                day.id = id_service.new_day_id(day.date)
            _ensure_unique_schedule_ids(day.schedules)
        return new_data

    old_by_date: dict[str, DailyItinerary] = {d.date: d for d in context.itinerary}
    old_by_id: dict[str, DailyItinerary] = {d.id: d for d in context.itinerary}

    for day in new_data.itinerary:
        old_day = None
        if _valid_day_id(day.id) and day.id in old_by_id:
            old_day = old_by_id[day.id]
        elif day.date in old_by_date:
            old_day = old_by_date[day.date]
            day.id = old_day.id

        if old_day is None:
            # New day.
            if not _valid_day_id(day.id):
                day.id = id_service.new_day_id(day.date)
            _ensure_unique_schedule_ids(day.schedules)
        else:
            day.schedules = _align_schedules(day.schedules, old_day.schedules)

    return new_data


def _ensure_unique_schedule_ids(schedules: list[Schedule]) -> None:
    """Replace invalid or duplicate IDs without changing unique valid IDs."""
    seen_ids: set[str] = set()
    for sch in schedules:
        if not _valid_schedule_id(sch.id) or sch.id in seen_ids:
            sch.id = id_service.new_schedule_id()
        seen_ids.add(sch.id)
