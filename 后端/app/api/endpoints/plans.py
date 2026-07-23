"""Plan endpoints: #3 list, #7 save (new), #8 update (overwrite), #11 delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.dependencies import get_current_user_id
from app.core.responses import success
from app.db.session import get_session
from app.models.dto import PlanSaveRequest
from app.services import plan_service

router = APIRouter(tags=["plans"])


@router.get("/plans")
def list_plans(
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    plans = plan_service.list_plans(session, current_user_id)
    return success([p.model_dump() for p in plans])


@router.post("/plans")
def create_plan(
    payload: PlanSaveRequest,
    current_user_id: str = Depends(get_current_user_id),
) -> dict:
    plan = plan_service.save_plan(current_user_id, payload.itinerary_data)
    return success(plan.model_dump())


@router.put("/plans/{plan_id}")
def update_plan(
    plan_id: str,
    payload: PlanSaveRequest,
    current_user_id: str = Depends(get_current_user_id),
) -> dict:
    plan = plan_service.update_plan(current_user_id, plan_id, payload.itinerary_data)
    return success(plan.model_dump())


@router.delete("/plans/{plan_id}")
def delete_plan(
    plan_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict:
    plan_service.delete_plan(current_user_id, plan_id)
    return success(None)
