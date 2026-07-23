"""Stable business-prefixed ID generation.

All primary keys are string ids: business prefix + UUID short code
(《数据库表结构与迁移规范》二.2). Never rely on auto-increment ids.
"""

from __future__ import annotations

import uuid

# Prefixes per spec (《数据库表结构与迁移规范》二.2 与《数据结构与通信接口规范》1.9).
PREFIX_POSTCARD_GROUP = "pg_"
PREFIX_POSTCARD = "pc_"
PREFIX_REPORT = "rpt_"
PREFIX_PLAN = "plan_"
PREFIX_ASSET = "asset_"
PREFIX_FILE_ASSET_REFERENCE = "far_"
PREFIX_MEMORY = "mem_"
PREFIX_MEMORY_EVENT = "mev_"
PREFIX_TOOL_CALL_LOG = "tool_"
PREFIX_DAY = "day_"
PREFIX_SCHEDULE = "sch_"


def _short_code() -> str:
    """Return a compact, collision-resistant UUID hex short code."""
    return uuid.uuid4().hex[:12]


def new_id(prefix: str) -> str:
    """Generate a new id with the given business prefix."""
    return f"{prefix}{_short_code()}"


def new_postcard_group_id() -> str:
    return new_id(PREFIX_POSTCARD_GROUP)


def new_postcard_id() -> str:
    return new_id(PREFIX_POSTCARD)


def new_report_id() -> str:
    return new_id(PREFIX_REPORT)


def new_plan_id() -> str:
    return new_id(PREFIX_PLAN)


def new_asset_id() -> str:
    return new_id(PREFIX_ASSET)


def new_reference_id() -> str:
    return new_id(PREFIX_FILE_ASSET_REFERENCE)


def new_memory_id() -> str:
    return new_id(PREFIX_MEMORY)


def new_memory_event_id() -> str:
    return new_id(PREFIX_MEMORY_EVENT)


def new_tool_call_log_id() -> str:
    return new_id(PREFIX_TOOL_CALL_LOG)


def new_day_id(date_iso: str | None = None) -> str:
    """Day id. Uses date digits when available for readability, else UUID."""
    if date_iso:
        digits = date_iso.replace("-", "")
        if digits.isdigit():
            return f"{PREFIX_DAY}{digits}"
    return new_id(PREFIX_DAY)


def new_schedule_id() -> str:
    return new_id(PREFIX_SCHEDULE)
