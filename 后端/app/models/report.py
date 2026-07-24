"""`reports` table (《数据库表结构与迁移规范》六)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Index
from sqlalchemy import JSON as SA_JSON
from sqlalchemy import Text
from sqlmodel import Field, SQLModel

from app.models.base import utcnow


class Report(SQLModel, table=True):
    __tablename__ = "reports"
    __table_args__ = (
        Index("idx_reports_user_created", "user_id", "created_at"),
        Index("idx_reports_user_location", "user_id", "location"),
    )

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    location: str = Field(nullable=False)
    start_date: str | None = Field(default=None, nullable=True)
    end_date: str | None = Field(default=None, nullable=True)
    date_label: str = Field(nullable=False)
    cover_image: str = Field(nullable=False)
    personality_summary: str = Field(nullable=False)
    content: str = Field(sa_column=Column(Text, nullable=False))
    # ReportChartPoint[] — five radar dimensions, validated at service/DTO layer.
    chart_data: list[Any] = Field(default_factory=list, sa_column=Column(SA_JSON, nullable=False))
    # V2 "旅行人格星球" payload. Nullable for full backward compatibility.
    profile_version: int | None = Field(default=None, nullable=True)
    profile_data: dict[str, Any] | None = Field(
        default=None, sa_column=Column(SA_JSON, nullable=True)
    )
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
