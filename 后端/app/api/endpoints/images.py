"""Image upload endpoint: #4 POST /images/upload (multipart/form-data)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.core.dependencies import get_current_user_id
from app.core.responses import success
from app.services import image_service

router = APIRouter(tags=["images"])


@router.post("/images/upload")
def upload_image(
    file: UploadFile = File(...),
    current_user_id: str = Depends(get_current_user_id),
) -> dict:
    content = file.file.read()
    result = image_service.save_uploaded_image(
        current_user_id, content, file.filename, file.content_type
    )
    return success(result.model_dump())
