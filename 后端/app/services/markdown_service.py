"""Jinja2 Markdown rendering for Plan `content`.

The preview `content` is produced deterministically by iterating
`itineraryData` through an external `.md` template — never by calling the
model (《数据结构与通信接口规范》3.7、《任务详细流程规范》八). Low latency,
fully reproducible.
"""

from __future__ import annotations

import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models.itinerary import ItineraryData
from app.services import date_label_service

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(enabled_extensions=(), default=False),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


def _day_label(date_str: str, title: str | None) -> str:
    label = date_label_service.generate_label(date_str, date_str) or date_str
    if title:
        return f"{label} · {title}"
    return label


def _time_range(start_time: str | None, end_time: str | None) -> str | None:
    if start_time and end_time:
        return f"{start_time}-{end_time}"
    if start_time:
        return start_time
    return None


def render_plan_content(itinerary_data: ItineraryData, date_label: str) -> str:
    """Render the Markdown preview text for a saved Plan."""
    days = [
        {
            "label": _day_label(day.date, day.title),
            "schedules": [
                {
                    "time_period": sch.time_period,
                    "activity": sch.activity,
                    "time_range": _time_range(sch.start_time, sch.end_time),
                }
                for sch in day.schedules
            ],
        }
        for day in itinerary_data.itinerary
    ]

    template = _env.get_template("plan_content.md")
    rendered = template.render(
        destination=itinerary_data.trip_info.destination,
        date_label=date_label,
        preparations=[p.model_dump() for p in itinerary_data.preparations],
        bookings=[b.model_dump() for b in itinerary_data.bookings],
        food_recommendations=list(itinerary_data.food_recommendations),
        days=days,
    )
    return rendered.strip() + "\n"
