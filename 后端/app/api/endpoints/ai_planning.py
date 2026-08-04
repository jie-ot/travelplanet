"""AI planning endpoint: #6 POST /ai/planning."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.business_logging import business_task
from app.core.dependencies import get_current_user_id
from app.core.responses import success
from app.models.dto import PlanningRequest
from app.services import planning_service

router = APIRouter(tags=["ai-planning"])


@router.post("/ai/planning")
def ai_planning(
    payload: PlanningRequest,
    current_user_id: str = Depends(get_current_user_id),
) -> dict:
    with business_task(
        "ai_planning",
        user_id=current_user_id,
        has_context=payload.context is not None,
        message_chars=len(payload.message or ""),
        planning_model=payload.planning_model,
        conversation_turns=len(payload.messages),
    ):
        result = planning_service.plan(current_user_id, payload)
    return success(result.model_dump())
