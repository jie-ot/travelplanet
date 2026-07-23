"""AI generation endpoint: #5 POST /generate."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.business_logging import business_task
from app.core.dependencies import get_current_user_id
from app.core.responses import success
from app.models.dto import GenerateRequest
from app.services import generation_service

router = APIRouter(tags=["generate"])


@router.post("/generate")
def generate(
    payload: GenerateRequest,
    current_user_id: str = Depends(get_current_user_id),
) -> dict:
    with business_task(
        "generate",
        user_id=current_user_id,
        photo_count=len(payload.photos),
        generate_postcards=payload.options.generate_postcards,
        generate_report=payload.options.generate_report,
    ):
        result = generation_service.generate(current_user_id, payload)
    return success(result.model_dump())
