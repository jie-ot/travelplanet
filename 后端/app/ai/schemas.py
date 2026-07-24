"""Model-internal output schemas (《后端与大模型通信接口规范》七).

These constrain MODEL output only; they do not replace front/back-end DTOs.
`ItineraryData` is reused from the contract (1.9) and `ReportChartPoint` from
1.7. Any new field that affects DTO/persistence/response must be reflected in
the contract spec first.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.dto import ReportChartPoint, TravelProfileData

__all__ = [
    "PhotoAnalysisItem",
    "PhotoAnalysisResult",
    "PostcardSelectionItem",
    "PostcardSelectionResult",
    "PostcardPlanItem",
    "PostcardPlanResult",
    "ReportChartPoint",
    "ReportDraftResult",
    "MemoryUpdateResult",
]


class PhotoAnalysisItem(BaseModel):
    asset_id: str
    scene_summary: str
    location_guess: str | None = None
    taken_date_guess: str | None = None
    suitability: Literal["good", "usable", "unsuitable"]
    postcard_reason: str | None = None
    report_reason: str | None = None


class PhotoAnalysisResult(BaseModel):
    photos: list[PhotoAnalysisItem]
    overall_location: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class PostcardSelectionItem(BaseModel):
    source_asset_ids: list[str]


class PostcardSelectionResult(BaseModel):
    items: list[PostcardSelectionItem]


class PostcardPlanItem(BaseModel):
    design_concept: str
    photo_transformation: str
    visual_device: str
    typography: str
    title: str
    source_asset_ids: list[str]
    extra_texts: list[str] = Field(default_factory=list)
    image_prompt: str


class PostcardPlanResult(BaseModel):
    items: list[PostcardPlanItem]


class ReportDraftResult(BaseModel):
    location: str
    start_date: str | None
    end_date: str | None
    personality_summary: str
    content: str
    chart_data: list[ReportChartPoint]
    profile_data: TravelProfileData


class MemoryUpdateResult(BaseModel):
    add_preferences: list[str] = []
    weaken_preferences: list[str] = []
    evidence_summary: str
    confidence: float
    source_task: Literal["generate", "plan_save", "plan_update"]
