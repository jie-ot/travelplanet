"""Deterministic state and confirmation rules for multi-turn trip intake."""

from __future__ import annotations

from datetime import date

from app.models.dto import PlanningBrief, PlanningChecklistItem

_NULL_TEXT = {"", "null", "none", "未知", "未说明", "待确认"}


def normalize_brief(brief: PlanningBrief) -> PlanningBrief:
    """Clean model-produced requirement state before it reaches the client."""
    brief.origin = _clean_optional(brief.origin)
    brief.destinations = _clean_list(brief.destinations)
    brief.start_date = _clean_date(brief.start_date)
    brief.end_date = _clean_date(brief.end_date)
    brief.traveler_count = (
        brief.traveler_count
        if brief.traveler_count is not None and brief.traveler_count > 0
        else None
    )
    brief.budget = _clean_optional(brief.budget)
    brief.transport_preference = _clean_optional(brief.transport_preference)
    brief.lodging_preference = _clean_optional(brief.lodging_preference)
    brief.interests = _clean_list(brief.interests)
    brief.constraints = _clean_list(brief.constraints)
    brief.assumptions = _clean_list(brief.assumptions)
    brief.summary = brief.summary.strip()
    return brief


def missing_required_fields(brief: PlanningBrief) -> list[str]:
    missing: list[str] = []
    if not brief.origin:
        missing.append("origin")
    if not brief.destinations:
        missing.append("destinations")
    if not brief.start_date:
        missing.append("startDate")
    if not brief.end_date:
        missing.append("endDate")
    if brief.start_date and brief.end_date:
        try:
            start = date.fromisoformat(brief.start_date)
            end = date.fromisoformat(brief.end_date)
        except ValueError:
            missing.extend(["startDate", "endDate"])
        else:
            if end < start:
                missing.extend(["startDate", "endDate"])
    return list(dict.fromkeys(missing))


def build_checklist(brief: PlanningBrief) -> list[PlanningChecklistItem]:
    destinations = "、".join(brief.destinations)
    dates = (
        f"{brief.start_date} 至 {brief.end_date}"
        if brief.start_date and brief.end_date
        else "还需要确认"
    )
    return [
        _item("origin", "从哪里出发", brief.origin, required=True),
        _item("destinations", "去哪里", destinations, required=True),
        _item("dates", "出行日期", dates if dates != "还需要确认" else None, required=True),
        _item(
            "travelers",
            "同行人数",
            f"{brief.traveler_count} 人" if brief.traveler_count else "未说明，暂按 1 人考虑",
            required=False,
            assumed=brief.traveler_count is None,
        ),
        _item(
            "lodging",
            "住宿偏好",
            brief.lodging_preference or "未限定，优先位置与动线",
            required=False,
            assumed=brief.lodging_preference is None,
        ),
        _item(
            "interests",
            "旅行重点",
            "、".join(brief.interests) or "未限定，兼顾经典体验与舒适节奏",
            required=False,
            assumed=not brief.interests,
        ),
    ]


def confirmed_requirement_text(brief: PlanningBrief, latest_message: str) -> str:
    """Create a compact, authoritative requirement block for itinerary research."""
    return "\n".join(
        [
            "用户已经确认以下旅行需求，请据此研究并生成完整行程：",
            f"- 出发地：{brief.origin}",
            f"- 目的地：{'、'.join(brief.destinations)}",
            f"- 日期：{brief.start_date} 至 {brief.end_date}",
            f"- 同行人数：{brief.traveler_count or 1} 人"
            + ("（用户未说明，按默认值）" if brief.traveler_count is None else ""),
            f"- 预算：{brief.budget or '未限定，采用舒适实用方案'}",
            f"- 大交通：{brief.transport_preference or '由你根据可执行性推荐'}",
            f"- 住宿：{brief.lodging_preference or '优先位置、交通和动线'}",
            f"- 兴趣：{'、'.join(brief.interests) or '经典体验与舒适节奏'}",
            f"- 约束：{'；'.join(brief.constraints) or '无额外约束'}",
            f"- 已确认假设：{'；'.join(brief.assumptions) or '无'}",
            f"- 用户确认语：{latest_message.strip()}",
        ]
    )


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return None if cleaned.lower() in _NULL_TEXT else cleaned


def _clean_date(value: str | None) -> str | None:
    cleaned = _clean_optional(value)
    if cleaned is None:
        return None
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError:
        return None


def _clean_list(values: list[str]) -> list[str]:
    cleaned = [
        item
        for value in values
        if (item := _clean_optional(value)) is not None
    ]
    return list(dict.fromkeys(cleaned))


def _item(
    key: str,
    label: str,
    value: str | None,
    *,
    required: bool,
    assumed: bool = False,
) -> PlanningChecklistItem:
    if not value:
        status = "missing"
        display = "还需要确认"
    elif assumed:
        status = "assumed"
        display = value
    else:
        status = "ready"
        display = value
    return PlanningChecklistItem(
        key=key,
        label=label,
        value=display,
        status=status,
        required=required,
    )
