"""Postcard group endpoints: #1 list, #9 delete.

Thin controllers: inject `current_user_id` + session, call service, wrap in the
standard response envelope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.dependencies import get_current_user_id
from app.core.responses import success
from app.db.session import get_session
from app.services import postcard_service

router = APIRouter(tags=["postcard-groups"])


@router.get("/postcard-groups")
def list_postcard_groups(
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    groups = postcard_service.list_postcard_groups(session, current_user_id)
    return success([g.model_dump() for g in groups])


@router.delete("/postcard-groups/{group_id}")
def delete_postcard_group(
    group_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict:
    postcard_service.delete_postcard_group(current_user_id, group_id)
    return success(None)
