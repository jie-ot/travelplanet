"""Tool-internal Schemas (《外部事实源与工具调用规范》四).

These are model-internal structures, never replacing front/back-end DTOs. The
`FactStatus` enum and the layering (A / A′ / B) must not be changed.
`TravelFactPack` is the ONLY fact container the model ever sees.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

FactStatus = Literal[
    "ok",
    "unknown",
    "timeout",
    "provider_not_connected",
    "needs_official_confirmation",
]


class ToolCallResult(BaseModel):
    tool_name: str
    provider: str  # amap / qweather / rail_query_mcp / flight_query_mcp / entry_guide
    status: FactStatus
    degraded_to_b: bool = False  # A′ failed → degraded to its B entry guide
    latency_ms: int | None = None
    error_code: str | None = None


class RouteFact(BaseModel):  # amap_route
    origin: str
    destination: str
    mode: Literal["driving", "transit", "walking", "bicycling"]
    distance_km: float | None
    duration_minutes: int | None
    status: FactStatus


class WeatherFact(BaseModel):  # amap_weather / qweather_forecast
    city: str
    date: str  # YYYY-MM-DD
    summary: str | None  # e.g. "多云 18-26℃"
    status: FactStatus


class PoiFact(BaseModel):  # amap_poi_search / amap_geocode
    name: str
    address: str | None
    location: str | None  # "lng,lat"
    category: str | None
    status: FactStatus


class BookingEvidence(BaseModel):  # B-class entry guide + A-class location facts
    booking_type: str  # e.g. "火车票"、"机票"、"酒店"、"景区门票"
    official_channel: str  # e.g. "12306 官方 App/网站"
    query_hint: str  # e.g. "2026-07-10 大理→丽江 动车"
    notes: str | None  # general notes (候补、提前预约等), no fabricated real-time data
    status: FactStatus  # B-class is always needs_official_confirmation


class RailFact(BaseModel):  # A′ rail_query_mcp (reference-level, non-authoritative)
    origin: str
    destination: str
    date: str  # YYYY-MM-DD
    train_no: str | None
    depart_time: str | None
    arrive_time: str | None
    duration: str | None = None  # 历时, e.g. "05:53"
    seat_class: str | None
    ref_price: float | None
    source: str = "community_mcp"
    status: FactStatus  # even ok is reference-level; model must add "以官方为准"


class FlightFact(BaseModel):  # A′ flight_query_mcp (reference-level, non-authoritative)
    origin: str
    destination: str
    date: str  # YYYY-MM-DD
    flight_no: str | None
    depart_time: str | None
    arrive_time: str | None
    aircraft: str | None
    ref_price: float | None
    source: str = "community_mcp"
    status: FactStatus


class TravelFactPack(BaseModel):  # the only fact container injected into the prompt
    request_id: str
    generated_at: str  # ISO 8601
    routes: list[RouteFact] = []
    weather: list[WeatherFact] = []
    pois: list[PoiFact] = []
    rails: list[RailFact] = []  # A′ rail reference facts (empty when degraded)
    flights: list[FlightFact] = []  # A′ flight reference facts (empty when degraded)
    booking_evidences: list[BookingEvidence] = []
    tool_calls: list[ToolCallResult] = []
