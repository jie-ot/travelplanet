"""`tool_call_logs` table (《数据库表结构与迁移规范》十二).

Desensitized audit of controlled external fact sources / MCP / Function
Calling. Never log AppKeys, tokens, full prompts or full implicit traits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Index
from sqlalchemy import JSON as SA_JSON
from sqlmodel import Field, SQLModel

from app.models.base import utcnow


class ToolCallLog(SQLModel, table=True):
    __tablename__ = "tool_call_logs"
    __table_args__ = (
        Index("idx_tool_call_logs_user", "user_id"),
        Index("idx_tool_call_logs_request", "request_id"),
        Index("idx_tool_call_logs_task", "task_type"),
    )

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    request_id: str = Field(index=True, nullable=False)
    # planning / generate ...
    task_type: str = Field(index=True, nullable=False)
    tool_name: str = Field(nullable=False)
    provider: str | None = Field(default=None, nullable=True)
    input_summary: dict[str, Any] | None = Field(
        default=None, sa_column=Column(SA_JSON, nullable=True)
    )
    output_summary: dict[str, Any] | None = Field(
        default=None, sa_column=Column(SA_JSON, nullable=True)
    )
    # success / timeout / failed / fallback
    status: str = Field(nullable=False)
    degraded_to_b: bool = Field(default=False, nullable=False)
    latency_ms: int | None = Field(default=None, nullable=True)
    error_code: str | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
