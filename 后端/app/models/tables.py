"""Import all SQLModel table entities so that `SQLModel.metadata` is fully
populated (used by Alembic autogenerate and runtime). Import this module
wherever the full schema must be registered.
"""

from __future__ import annotations

from app.models.file_asset import FileAsset
from app.models.file_asset_reference import FileAssetReference
from app.models.plan import Plan
from app.models.postcard import Postcard
from app.models.postcard_group import PostcardGroup
from app.models.report import Report
from app.models.tool_call_log import ToolCallLog
from app.models.user_memory import UserMemory
from app.models.user_memory_event import UserMemoryEvent

__all__ = [
    "FileAsset",
    "FileAssetReference",
    "Plan",
    "Postcard",
    "PostcardGroup",
    "Report",
    "ToolCallLog",
    "UserMemory",
    "UserMemoryEvent",
]
