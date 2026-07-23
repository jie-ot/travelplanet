"""`postcard_groups` table (《数据库表结构与迁移规范》四)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.base import utcnow


class PostcardGroup(SQLModel, table=True):
    __tablename__ = "postcard_groups"
    __table_args__ = (
        Index("idx_postcard_groups_user_created", "user_id", "created_at"),
        Index("idx_postcard_groups_user_location", "user_id", "location"),
    )

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    location: str = Field(nullable=False)
    start_date: str | None = Field(default=None, nullable=True)
    end_date: str | None = Field(default=None, nullable=True)
    date_label: str = Field(nullable=False)
    cover_image: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
