"""Structured itinerary data (《数据结构与通信接口规范》1.9).

This family is intentionally **snake_case** and does NOT participate in the
camelCase alias conversion used by the other outward DTOs — it is more natural
for the model and more stable for structured output. Reused by the AI internal
schemas and embedded inside the `Plan` DTO.
"""

from __future__ import annotations

from pydantic import BaseModel


class TripInfo(BaseModel):
    destination: str
    start_date: str
    end_date: str
    date_label: str


class Preparation(BaseModel):
    category: str
    items: str


class Booking(BaseModel):
    type: str
    details: str


class Schedule(BaseModel):
    id: str
    time_period: str
    start_time: str | None = None
    end_time: str | None = None
    activity: str
    transport: str | None = None
    note: str | None = None


class DailyItinerary(BaseModel):
    id: str
    date: str
    title: str | None = None
    schedules: list[Schedule]


class ItineraryData(BaseModel):
    trip_info: TripInfo
    preparations: list[Preparation]
    bookings: list[Booking]
    food_recommendations: list[str]
    itinerary: list[DailyItinerary]
