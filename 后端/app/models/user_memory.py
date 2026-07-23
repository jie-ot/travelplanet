"""`user_memories` table (《数据库表结构与迁移规范》十).

One current implicit-traits document per user. Read by all AI generate /
planning / save tasks; never hardcode memory in config or prompt files.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy import JSON as SA_JSON
from sqlalchemy import Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import utcnow


class UserMemory(SQLModel, table=True):
    __tablename__ = "user_memories"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_memories_user_id"),
    )

    id: str = Field(primary_key=True)
    user_id: str = Field(nullable=False)
    memory_text: str = Field(sa_column=Column(Text, nullable=False))
    # Optional structured profile: preference dimensions, evidence, confidence.
    memory_json: dict[str, Any] | None = Field(
        default=None, sa_column=Column(SA_JSON, nullable=True)
    )
    version: int = Field(default=1, nullable=False)
    # generate / plan_save / plan_update / manual
    last_source_type: str | None = Field(default=None, nullable=True)
    last_source_id: str | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
