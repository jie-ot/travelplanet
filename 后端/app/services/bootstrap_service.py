"""Startup baseline checks (no schema creation).

`ensure_demo_baseline` lightly verifies that the demo user's `user_memories`
row and the system default-cover `FileAsset` exist, creating them if missing
(《数据库表结构与迁移规范》十五.3). It must NOT create tables or replace
Alembic; if tables are missing it logs a clear configuration error telling the
operator to run `alembic upgrade head` first.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.core.config import settings
from app.services import id_service, memory_service, storage_service
from app.services.file_asset_service import STATUS_ATTACHED, SYSTEM_USER_ID

logger = logging.getLogger("travelplanet")

DEFAULT_COVER_RELATIVE_PATH = storage_service.DEFAULT_COVER_PATH


def _ensure_default_cover_file() -> int:
    """Make sure the physical default cover exists; return its size in bytes."""
    abs_path = storage_service.resolve_static_path(DEFAULT_COVER_RELATIVE_PATH)
    if os.path.exists(abs_path):
        return os.path.getsize(abs_path)

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    # Generate a simple placeholder cover so the demo never 404s on the cover.
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1024, 1024), color=(94, 129, 172))
    draw = ImageDraw.Draw(img)
    draw.rectangle([64, 64, 960, 960], outline=(236, 239, 244), width=8)
    draw.text((360, 480), "TRAVEL PLANET", fill=(236, 239, 244))
    img.save(abs_path, format="JPEG", quality=85)
    return os.path.getsize(abs_path)


def ensure_default_cover_asset(session: Session) -> None:
    """Ensure the system default-cover FileAsset row exists (no reference)."""
    from app.models.file_asset import FileAsset

    size_bytes = _ensure_default_cover_file()
    existing = session.exec(
        select(FileAsset).where(FileAsset.relative_path == DEFAULT_COVER_RELATIVE_PATH)
    ).first()
    if existing is not None:
        return

    asset = FileAsset(
        id=id_service.new_asset_id(),
        user_id=SYSTEM_USER_ID,
        relative_path=DEFAULT_COVER_RELATIVE_PATH,
        mime_type="image/jpeg",
        size_bytes=size_bytes,
        usage_type="system",
        status=STATUS_ATTACHED,
        ref_count=0,
    )
    session.add(asset)
    session.flush()
    logger.info("Created system default cover FileAsset %s", asset.id)


def ensure_demo_baseline(session: Session) -> None:
    """Idempotently ensure demo user memory + system default cover exist."""
    try:
        memory_service.get_or_create_current_memory(session, settings.DEFAULT_USER_ID)
        ensure_default_cover_asset(session)
        session.commit()
    except OperationalError:
        session.rollback()
        logger.error(
            "Database tables are missing. Run `uv run alembic upgrade head` "
            "before starting the server or seeding demo data."
        )
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("ensure_demo_baseline failed")
