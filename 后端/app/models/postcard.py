"""`postcards` table (《数据库表结构与迁移规范》五)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.base import utcnow


class Postcard(SQLModel, table=True):
    __tablename__ = "postcards"
    __table_args__ = (
        Index("idx_postcards_user_group", "user_id", "group_id", "sort_order"),
        Index("idx_postcards_user_created", "user_id", "created_at"),
    )

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    group_id: str = Field(foreign_key="postcard_groups.id", index=True, nullable=False)
    title: str = Field(nullable=False)
    image_url: str = Field(nullable=False)
    sort_order: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
