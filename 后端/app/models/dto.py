"""Outward DTOs — the single front/back-end contract.

Master definition: 《数据结构与通信接口规范》一、三. Field names, types,
optionality and enums are the project's unique contract; compatibility changes
must be additive and optional. All outward DTOs are camelCase via `to_camel`; the
`ItineraryData` family stays snake_case (see `app.models.itinerary`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.ai.model_selection import DEFAULT_PLANNING_MODEL, PlanningModel
from app.models.itinerary import ItineraryData

# Outward enums (mirrored from the DB enum table & 1.4/1.5/1.7).
RadarDimension = Literal["自然探索", "人文体验", "美食偏好", "慢节奏", "社交意愿"]
VisualTheme = Literal[
    "forest_light",
    "ocean_blue",
    "sunset_orange",
    "museum_gold",
    "city_neon",
    "night_purple",
    "snow_silver",
    "desert_amber",
]
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


class ProfileSpectrum(CamelModel):
    id: Literal["environment", "depth", "planning", "social"]
    left_label: str
    right_label: str
    value: int


class ProfileModule(CamelModel):
    title: str
    content: str


class TravelProfileData(CamelModel):
    archetype_id: str
    archetype_name: str
    persona_code: str
    slogan: str
    spectrums: list[ProfileSpectrum]
    keywords: list[str]
    modules: list[ProfileModule]
    next_trip_inspiration: str
    visual_theme: VisualTheme


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
    profile_version: int | None = None
    profile_data: TravelProfileData | None = None


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


PlanningPhase = Literal["collecting", "confirming", "completed"]
PlanningMessageRole = Literal["user", "assistant"]
PlanningChecklistStatus = Literal["ready", "assumed", "missing"]


class PlanningChatMessage(CamelModel):
    role: PlanningMessageRole
    content: str
    # Optional for backward compatibility. New clients stamp every real turn so
    # the stateless endpoint can reject model switching inside one conversation.
    planning_model: PlanningModel | None = None


class PlanningBrief(CamelModel):
    """Structured requirement state accumulated across planning chat turns."""

    origin: str | None = None
    destinations: list[str] = Field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    traveler_count: int | None = None
    budget: str | None = None
    transport_preference: str | None = None
    lodging_preference: str | None = None
    interests: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    summary: str = ""


class PlanningChecklistItem(CamelModel):
    key: str
    label: str
    value: str
    status: PlanningChecklistStatus
    required: bool


class PlanningRequest(CamelModel):
    message: str
    planning_model: PlanningModel = DEFAULT_PLANNING_MODEL
    # New plans collect requirements first. Existing plans may still be refined
    # directly by passing context + confirmed=true.
    context: ItineraryData | None = None
    messages: list[PlanningChatMessage] = Field(default_factory=list)
    brief: PlanningBrief | None = None
    confirmed: bool = False


class PlanningResponse(CamelModel):
    phase: PlanningPhase
    assistant_message: str
    planning_model: PlanningModel
    brief: PlanningBrief | None = None
    checklist: list[PlanningChecklistItem] = Field(default_factory=list)
    itinerary: ItineraryData | None = None


class PlanSaveRequest(CamelModel):
    itinerary_data: ItineraryData
