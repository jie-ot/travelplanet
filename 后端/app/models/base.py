"""Shared helpers for SQLModel entities.

Time fields are stored timezone-aware UTC (《数据库表结构与迁移规范》二.3);
serialization to ISO strings happens at the DTO layer. Date fields
(`start_date`/`end_date`) are nullable `YYYY-MM-DD` strings (二.4).
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)
