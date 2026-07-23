"""`file_asset_references` table (《数据库表结构与迁移规范》九).

Single source of truth for which business entities reference which assets.
Reference matrix master definition: 《后端技术栈与全局规范》3.2.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import utcnow


class FileAssetReference(SQLModel, table=True):
    __tablename__ = "file_asset_references"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "owner_type",
            "owner_id",
            "role",
            name="uq_far_asset_owner_role",
        ),
        Index("idx_far_owner", "user_id", "owner_type", "owner_id"),
        Index("idx_far_asset", "asset_id"),
    )

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    asset_id: str = Field(foreign_key="file_assets.id", index=True, nullable=False)
    # postcard_group / postcard / report / plan / user_memory
    owner_type: str = Field(index=True, nullable=False)
    owner_id: str = Field(index=True, nullable=False)
    # source_photo / postcard_image / report_cover / plan_attachment / memory_evidence
    role: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
