"""Outward DTOs — the single front/back-end contract.

Master definition: 《数据结构与通信接口规范》一、三. Field names, types,
optionality and enums are the project's unique contract; never add, rename or
change optionality. All outward DTOs are camelCase via `to_camel`; the
`ItineraryData` family stays snake_case (see `app.models.itinerary`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.models.itinerary import ItineraryData

# Outward enums (mirrored from the DB enum table & 1.4/1.5/1.7).
RadarDimension = Literal["自然探索", "人文体验", "美食偏好", "慢节奏", "社交意愿"]
# Fixed radar dimension order for stable chart rendering.
RADAR_DIMENSIONS: tuple[RadarDimension, ...] = (
    "自然探索",
    "人文体验",
    "美食偏好",
    "慢节奏",
    "社交意愿",
)

UsageType = Literal["upload", "generated_postcard", "generated_report_cover", "system"]
AssetStatus = Literal["temporary", "attached", "deleted"]
OwnerType = Literal["postcard_group", "postcard", "report", "plan", "user_memory"]
ReferenceRole = Literal[
    "source_photo", "postcard_image", "report_cover", "plan_attachment", "memory_evidence"
]


class CamelModel(BaseModel):
    """Base for outward DTOs: camelCase aliases, accept both names on input."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


# —— 1.2 Postcard ——
class Postcard(CamelModel):
    id: str
    title: str
    image_url: str


# —— 1.1 PostcardGroup ——
class PostcardGroup(CamelModel):
    id: str
    location: str
    start_date: str | None
    end_date: str | None
    date_label: str
    cover_image: str
    postcards: list[Postcard]


# —— 1.7 ReportChartPoint ——
class ReportChartPoint(CamelModel):
    dimension: RadarDimension
    value: int


# —— 1.6 Report ——
class Report(CamelModel):
    id: str
    location: str
    start_date: str | None
    end_date: str | None
    date_label: str
    cover_image: str
    personality_summary: str
    content: str
    chart_data: list[ReportChartPoint]


# —— 1.8 Plan —— (itineraryData stays snake_case)
class Plan(CamelModel):
    id: str
    location: str
    start_date: str | None
    end_date: str | None
    date_label: str
    content: str
    itinerary_data: ItineraryData


# —— 1.4 FileAsset ——
class FileAsset(CamelModel):
    id: str
    relative_path: str
    mime_type: str
    size_bytes: int
    usage_type: UsageType
    status: AssetStatus
    ref_count: int


# —— 1.3 UploadedPhoto ——
class UploadedPhoto(CamelModel):
    asset_id: str
    image_url: str
    taken_at: str | None
    location: str | None


# —— 1.5 FileAssetReference ——
class FileAssetReference(CamelModel):
    id: str
    user_id: str
    asset_id: str
    owner_type: OwnerType
    owner_id: str
    role: ReferenceRole
    created_at: str


# —— Upload response (#4) ——
class UploadResult(CamelModel):
    asset_id: str
    image_url: str


# —— Generate response (#5) ——
class GenerateResult(CamelModel):
    postcard_group: PostcardGroup | None
    report: Report | None


# ============================================================
# Request bodies
# ============================================================


class GenerateOptions(CamelModel):
    generate_postcards: bool
    generate_report: bool


class GenerateRequest(CamelModel):
    photos: list[UploadedPhoto]
    requirements: str
    options: GenerateOptions


class PlanningRequest(CamelModel):
    message: str
    # First turn is null; later turns carry the full ItineraryData (snake_case).
    context: ItineraryData | None


class PlanSaveRequest(CamelModel):
    itinerary_data: ItineraryData
