"""AI planning endpoint: #6 POST /ai/planning (+ its progress read)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core import planning_progress
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
        progress_token=planning_progress.normalize_token(payload.progress_token),
    ):
        with planning_progress.progress_session(
            payload.progress_token,
            planning_model=payload.planning_model,
        ):
            result = planning_service.plan(current_user_id, payload)
            planning_progress.finish()
    return success(result.model_dump())


@router.get("/ai/planning/progress")
def ai_planning_progress(
    token: str = Query(..., max_length=64),
) -> dict:
    """Read the live stage of an in-flight planning request.

    Read-only and side-effect free. An unknown or expired token returns
    ``null`` data rather than an error, because the client may poll a moment
    before the request registers or after it has been evicted.
    """
    return success(planning_progress.snapshot(token))
