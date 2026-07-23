"""Report endpoints: #2 list, #10 delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.dependencies import get_current_user_id
from app.core.responses import success
from app.db.session import get_session
from app.services import report_service

router = APIRouter(tags=["reports"])


@router.get("/reports")
def list_reports(
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    reports = report_service.list_reports(session, current_user_id)
    return success([r.model_dump() for r in reports])


@router.delete("/reports/{report_id}")
def delete_report(
    report_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict:
    report_service.delete_report(current_user_id, report_id)
    return success(None)
