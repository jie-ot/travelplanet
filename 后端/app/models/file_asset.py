"""`file_assets` table (《数据库表结构与迁移规范》八).

Lifecycle / state machine master definition: 《后端技术栈与全局规范》三.
`ref_count` is a cache; `file_asset_references` is the single source of truth.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import utcnow


class FileAsset(SQLModel, table=True):
    __tablename__ = "file_assets"
    __table_args__ = (
        UniqueConstraint("relative_path", name="uq_file_assets_relative_path"),
        Index("idx_file_assets_user_status", "user_id", "status"),
        Index("idx_file_assets_usage", "usage_type"),
    )

    id: str = Field(primary_key=True)
    # Owning user; system default image fixed to "system".
    user_id: str = Field(index=True, nullable=False)
    relative_path: str = Field(nullable=False)
    mime_type: str = Field(nullable=False)
    size_bytes: int = Field(nullable=False)
    # upload / generated_postcard / generated_report_cover / system
    usage_type: str = Field(nullable=False)
    # temporary / attached / deleted
    status: str = Field(nullable=False)
    ref_count: int = Field(default=0, nullable=False)
    storage_backend: str = Field(default="local", nullable=False)
    checksum: str | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True)
