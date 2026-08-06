"""Structured itinerary data (《数据结构与通信接口规范》1.9).

This family is intentionally **snake_case** and does NOT participate in the
camelCase alias conversion used by the other outward DTOs — it is more natural
for the model and more stable for structured output. Reused by the AI internal
schemas and embedded inside the `Plan` DTO.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TripInfo(BaseModel):
    destination: str
    start_date: str
    end_date: str
    date_label: str


class Preparation(BaseModel):
    category: str
    items: str


ALLOWED_BOOKING_TYPES = frozenset({"机票", "火车票", "酒店", "景区门票"})


class Booking(BaseModel):
    type: str
    details: str

    @field_validator("type")
    @classmethod
    def normalize_booking_type(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class ScheduleAction(BaseModel):
    type: Literal["map", "booking", "details", "alternative", "complete"]
    label: str


class Schedule(BaseModel):
    id: str
    time_period: str
    start_time: str | None = None
    end_time: str | None = None
    activity: str
    transport: str | None = None
    place_name: str | None = None
    location: str | None = None
    travel_minutes: int | None = None
    distance_km: float | None = None
    transport_mode: Literal["driving", "transit", "walking", "bicycling"] | None = None
    tags: list[str] = Field(default_factory=list)
    booking_required: bool = False
    fact_status: Literal["verified", "reference", "unverified"] | None = None
    fact_refs: list[str] = Field(default_factory=list)
    action: ScheduleAction | None = None
    # DeepSeek-only semantic hints. The backend validates these before it builds
    # a static play map; other models and older saved itineraries omit them.
    map_role: Literal["hotel", "attraction"] | None = None
    map_group: str | None = Field(default=None, max_length=24)
    map_label: str | None = Field(default=None, max_length=32)

    @field_validator("transport_mode", mode="before")
    @classmethod
    def normalize_long_distance_transport_mode(cls, value: object) -> object:
        """Keep long-distance mode in `transport`; it is not an AMap route mode."""
        if isinstance(value, str) and value.strip().lower() in {
            "rail",
            "train",
            "high_speed_rail",
            "flight",
            "air",
            "rail/air",
            "rail/flight",
        }:
            return None
        return value


class DailyMapPoint(BaseModel):
    schedule_id: str
    marker: str
    name: str
    location: str
    kind: Literal["hotel", "attraction"]


class DailyMapLeg(BaseModel):
    origin_marker: str
    destination_marker: str
    transport_text: str


class DailyMap(BaseModel):
    id: str
    title: str
    image_url: str | None = None
    status: Literal["ready", "unavailable"] = "unavailable"
    points: list[DailyMapPoint] = Field(default_factory=list)
    legs: list[DailyMapLeg] = Field(default_factory=list)
    line_note: str = "游玩顺序示意线"


class DailyItinerary(BaseModel):
    id: str
    date: str
    title: str | None = None
    schedules: list[Schedule]
    daily_maps: list[DailyMap] = Field(default_factory=list)


class ExperienceSummary(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
    )

    trip_theme: str = Field(default="", alias="tripTheme")
    pace: str = ""
    intensity: int = 50
    highlights: list[str] = Field(default_factory=list)
    weather_summary: str = Field(default="", alias="weatherSummary")
    personalization_tags: list[str] = Field(
        default_factory=list, alias="personalizationTags"
    )


class ItineraryData(BaseModel):
    trip_info: TripInfo
    preparations: list[Preparation]
    bookings: list[Booking]
    food_recommendations: list[str]
    itinerary: list[DailyItinerary]
    experience_summary: ExperienceSummary | None = None
    # Filled by the feasibility gate, never by the model: findings that survive
    # repair but belong to a whole day or to the booking list have no schedule row
    # to carry a caveat, and shipping them silently is how a plan that contradicts
    # the request reaches the traveller looking fully verified.
    advisories: list[str] = Field(default_factory=list)

    @field_validator("bookings", mode="after")
    @classmethod
    def keep_allowed_booking_types(cls, value: list[Booking]) -> list[Booking]:
        """预订指南只保留机票/火车票/酒店/景区门票中真实存在的项。"""
        return [item for item in value if item.type in ALLOWED_BOOKING_TYPES]
