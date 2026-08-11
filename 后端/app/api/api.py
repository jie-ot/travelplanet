"""Aggregate API router.

Individual endpoint routers are registered here in later phases. Kept thin on
purpose; all routes live under the `/api` prefix applied in `app.main`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.endpoints import (
    ai_planning,
    generate,
    images,
    memories,
    plans,
    postcard_groups,
    reports,
)

api_router = APIRouter()

api_router.include_router(postcard_groups.router)
api_router.include_router(reports.router)
api_router.include_router(plans.router)
api_router.include_router(images.router)
api_router.include_router(generate.router)
api_router.include_router(ai_planning.router)
api_router.include_router(memories.router)
