"""`user_memory_events` table (《数据库表结构与迁移规范》十一).

Incremental evidence / audit trail for implicit traits. Table is always
created; writing on each merge is recommended.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Index
from sqlalchemy import JSON as SA_JSON
from sqlmodel import Field, SQLModel

from app.models.base import utcnow


class UserMemoryEvent(SQLModel, table=True):
    __tablename__ = "user_memory_events"
    __table_args__ = (
        Index("idx_user_memory_events_user", "user_id"),
        Index("idx_user_memory_events_memory", "memory_id"),
    )

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    memory_id: str = Field(index=True, nullable=False)
    # generate / plan_save / plan_update / manual
    source_type: str = Field(nullable=False)
    source_id: str | None = Field(default=None, nullable=True)
    delta_json: dict[str, Any] | None = Field(
        default=None, sa_column=Column(SA_JSON, nullable=True)
    )
    version_after: int = Field(nullable=False)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
