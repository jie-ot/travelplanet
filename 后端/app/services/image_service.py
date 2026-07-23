"""Upload image service (#4): validate + persist + temporary FileAsset.

Combines `storage_service` (validation, UUID filename, disk write) with
`FileAssetService` (two-phase lifecycle, starts as `temporary`). The asset is
flipped to `attached` only later by `/api/generate` (《任务详细流程规范》五).
"""

from __future__ import annotations

import logging

from app.core.exceptions import InternalError
from app.db.session import session_scope
from app.models import dto
from app.services import file_asset_service, storage_service

logger = logging.getLogger("travelplanet")


def save_uploaded_image(
    user_id: str,
    content: bytes,
    filename: str | None,
    content_type: str | None,
) -> dto.UploadResult:
    """Validate & store an uploaded image, returning {assetId, imageUrl}."""
    stored = storage_service.save_upload(content, filename, content_type)
    try:
        with session_scope() as session:
            asset = file_asset_service.create_temporary(
                session,
                user_id=user_id,
                relative_path=stored.relative_path,
                mime_type=stored.mime_type,
                size_bytes=stored.size_bytes,
                usage_type="upload",
            )
            asset_id = asset.id
    except Exception as exc:  # noqa: BLE001
        # Physical file written but DB record failed → clean up the orphan.
        storage_service.delete_physical_file(stored.relative_path)
        raise InternalError("上传记录创建失败") from exc

    return dto.UploadResult(asset_id=asset_id, image_url=stored.relative_path)
