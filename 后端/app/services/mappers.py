"""Entity → outward DTO mappers.

Keeps DB entities (snake_case columns) and outward DTOs (camelCase) decoupled.
The model/tool outputs never reach here directly — only validated, persisted
entities are mapped to DTOs.
"""

from __future__ import annotations

from app.models import dto
from app.models.itinerary import ItineraryData
from app.models.plan import Plan as PlanEntity
from app.models.postcard import Postcard as PostcardEntity
from app.models.postcard_group import PostcardGroup as PostcardGroupEntity
from app.models.report import Report as ReportEntity


def postcard_to_dto(entity: PostcardEntity) -> dto.Postcard:
    return dto.Postcard(id=entity.id, title=entity.title, image_url=entity.image_url)


def postcard_group_to_dto(
    group: PostcardGroupEntity, postcards: list[PostcardEntity]
) -> dto.PostcardGroup:
    return dto.PostcardGroup(
        id=group.id,
        location=group.location,
        start_date=group.start_date,
        end_date=group.end_date,
        date_label=group.date_label,
        cover_image=group.cover_image,
        postcards=[postcard_to_dto(pc) for pc in postcards],
    )


def report_to_dto(entity: ReportEntity) -> dto.Report:
    chart = [dto.ReportChartPoint.model_validate(point) for point in (entity.chart_data or [])]
    return dto.Report(
        id=entity.id,
        location=entity.location,
        start_date=entity.start_date,
        end_date=entity.end_date,
        date_label=entity.date_label,
        cover_image=entity.cover_image,
        personality_summary=entity.personality_summary,
        content=entity.content,
        chart_data=chart,
    )


def plan_to_dto(entity: PlanEntity) -> dto.Plan:
    return dto.Plan(
        id=entity.id,
        location=entity.location,
        start_date=entity.start_date,
        end_date=entity.end_date,
        date_label=entity.date_label,
        content=entity.content,
        itinerary_data=ItineraryData.model_validate(entity.itinerary_data),
    )
