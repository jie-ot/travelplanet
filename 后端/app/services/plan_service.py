"""Plan business service: list / save / update / delete.

Master flows: 《任务详细流程规范》四、八、九、十二. On save/update the backend
extracts destination/start/end from `trip_info`, generates `dateLabel` itself,
renders Markdown `content` via Jinja2 (never the model), writes in a short
transaction, then merges a deterministic memory increment in an INDEPENDENT
transaction (a memory failure never rolls back a saved Plan).
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.core.exceptions import NotFoundError
from app.db.session import session_scope
from app.models import dto
from app.models.itinerary import ItineraryData
from app.models.plan import Plan as PlanEntity
from app.services import (
    date_label_service,
    file_asset_service,
    id_service,
    itinerary_validation_service,
    mappers,
    markdown_service,
    memory_service,
)

logger = logging.getLogger("travelplanet")


def list_plans(session: Session, user_id: str) -> list[dto.Plan]:
    """List the user's plans, newest first."""
    plans = session.exec(
        select(PlanEntity)
        .where(PlanEntity.user_id == user_id)
        .order_by(PlanEntity.created_at.desc())
    ).all()
    return [mappers.plan_to_dto(p) for p in plans]


def _apply_derived_fields(entity: PlanEntity, data: ItineraryData) -> None:
    """Fill main-table fields derived from itinerary_data (deterministic)."""
    trip = data.trip_info
    entity.location = trip.destination
    entity.start_date = trip.start_date
    entity.end_date = trip.end_date
    entity.date_label = date_label_service.generate_label(trip.start_date, trip.end_date)
    entity.content = markdown_service.render_plan_content(data, entity.date_label)
    entity.itinerary_data = data.model_dump()


def save_plan(user_id: str, data: ItineraryData) -> dto.Plan:
    """Create a new Plan (#7). Validates, derives fields, writes, then memory."""
    itinerary_validation_service.validate_itinerary(data)

    plan_id = id_service.new_plan_id()
    with session_scope() as session:
        entity = PlanEntity(id=plan_id, user_id=user_id, location="", date_label="", content="")
        _apply_derived_fields(entity, data)
        session.add(entity)
        session.flush()
        result = mappers.plan_to_dto(entity)

    _merge_memory(user_id, data, source_type="plan_save", source_id=plan_id)
    return result


def update_plan(user_id: str, plan_id: str, data: ItineraryData) -> dto.Plan:
    """Overwrite an existing Plan (#8). 1004 if not found. Then memory."""
    itinerary_validation_service.validate_itinerary(data)

    with session_scope() as session:
        entity = session.exec(
            select(PlanEntity).where(
                PlanEntity.id == plan_id,
                PlanEntity.user_id == user_id,
            )
        ).first()
        if entity is None:
            raise NotFoundError("规划不存在或已被删除")
        from app.models.base import utcnow

        _apply_derived_fields(entity, data)
        entity.updated_at = utcnow()
        session.add(entity)
        session.flush()
        result = mappers.plan_to_dto(entity)

    _merge_memory(user_id, data, source_type="plan_update", source_id=plan_id)
    return result


def delete_plan(user_id: str, plan_id: str) -> None:
    """Delete a Plan (#11). Releases any plan_attachment references."""
    with session_scope() as session:
        entity = session.exec(
            select(PlanEntity).where(
                PlanEntity.id == plan_id,
                PlanEntity.user_id == user_id,
            )
        ).first()
        if entity is None:
            raise NotFoundError("规划不存在或已被删除")
        file_asset_service.release_and_cleanup(
            session, owner_type="plan", owner_id=plan_id
        )
        session.delete(entity)
        session.flush()


def _merge_memory(
    user_id: str, data: ItineraryData, *, source_type: str, source_id: str
) -> None:
    """Deterministic implicit-traits增量 in an independent transaction.

    A memory failure is logged and swallowed — it must not roll back the Plan.
    """
    destination = data.trip_info.destination.strip()
    if not destination:
        return
    try:
        with session_scope() as session:
            memory_service.merge_memory_update(
                session,
                user_id=user_id,
                add_preferences=[f"目的地偏好：{destination}"],
                weaken_preferences=[],
                evidence_summary=f"用户保存了前往「{destination}」的行程规划。",
                confidence=0.3,
                source_type=source_type,
                source_id=source_id,
            )
    except Exception:  # noqa: BLE001
        logger.exception("memory merge failed for plan %s (non-fatal)", source_id)
