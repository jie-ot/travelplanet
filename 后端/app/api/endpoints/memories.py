"""User-facing travel memory endpoints."""

from __future__ import annotations

from sqlalchemy.orm.attributes import flag_modified
from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.dependencies import get_current_user_id
from app.core.exceptions import InvalidParamError
from app.core.responses import success
from app.db.session import get_session
from app.models.dto import (
    MemoryDescriptionUpdateRequest,
    MemoryOverviewUpdateRequest,
    MemoryPlanningPreferencesUpdateRequest,
)
from app.models.user_memory import UserMemory
from app.models.user_memory_event import UserMemoryEvent
from app.services.memory_display_adapter import MemoryDisplayAdapter
from app.services import id_service
from app.services.memory_service import get_or_create_current_memory, merge_memory_update, _render_memory_text
from app.models.base import utcnow

router = APIRouter(tags=["memories"])
adapter = MemoryDisplayAdapter()


def _forget_memory_summaries(
    session: Session,
    memory: UserMemory,
    summaries: list[str],
    source_id: str,
) -> UserMemory:
    targets = {summary.strip().lower() for summary in summaries if summary.strip()}
    mem_json = dict(memory.memory_json or {})
    hidden_ids = list(mem_json.get("hidden_display_ids", []))
    if source_id not in hidden_ids:
        hidden_ids.append(source_id)
    mem_json["hidden_display_ids"] = hidden_ids
    preferences = list(mem_json.get("preferences", []))
    for pref in preferences:
        if not isinstance(pref, dict):
            continue
        summary = str(pref.get("summary") or pref.get("key") or "").strip().lower()
        refs = pref.get("source_refs", [])
        if summary in targets or (isinstance(refs, list) and source_id in refs):
            pref["confidence"] = 0
            pref["status"] = "weakened"
            pref["last_seen"] = utcnow().isoformat()

    mem_json["preferences"] = preferences
    planning_preferences = mem_json.get("planning_preferences")
    memory.memory_json = mem_json
    flag_modified(memory, "memory_json")
    memory.memory_text = _render_memory_text(
        preferences,
        planning_preferences if isinstance(planning_preferences, dict) else None,
    )
    memory.version = int(memory.version) + 1
    memory.last_source_type = "manual"
    memory.last_source_id = source_id
    memory.updated_at = utcnow()
    session.add(memory)
    session.flush()
    session.add(
        UserMemoryEvent(
            id=id_service.new_memory_event_id(),
            user_id=memory.user_id,
            memory_id=memory.id,
            source_type="manual",
            source_id=source_id,
            delta_json={
                "delete_preferences": summaries,
                "evidence_summary": "用户在旅行记忆抽屉中删除一条长期记忆",
            },
            version_after=memory.version,
        )
    )
    session.flush()
    return memory


@router.get("/memories/travel")
def get_travel_memory(
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    memory = get_or_create_current_memory(session, current_user_id)
    session.commit()
    display = adapter.to_display(memory)
    return success(display.model_dump(by_alias=True))


@router.put("/memories/travel/descriptions/{description_id}")
def update_travel_memory_description(
    description_id: str,
    payload: MemoryDescriptionUpdateRequest,
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    title = payload.title.strip()
    content = payload.content.strip()
    if not title or not content:
        raise InvalidParamError("记忆内容不能为空")
    current_memory = get_or_create_current_memory(session, current_user_id)
    previous_summaries = adapter.source_text_for_description(current_memory, description_id)
    memory = merge_memory_update(
        session,
        user_id=current_user_id,
        add_preferences=[f"{title}\n{content}"],
        weaken_preferences=previous_summaries,
        evidence_summary="用户在旅行记忆抽屉中直接编辑长期记忆",
        confidence=0.7,
        source_type="manual",
        source_id=description_id,
    )
    session.commit()
    display = adapter.to_display(memory)
    return success(display.model_dump(by_alias=True))


@router.put("/memories/travel/overview")
def update_travel_memory_overview(
    payload: MemoryOverviewUpdateRequest,
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    title = payload.title.strip()
    content = payload.content.strip()
    if not title or not content:
        raise InvalidParamError("概述内容不能为空")

    memory = get_or_create_current_memory(session, current_user_id)
    mem_json = dict(memory.memory_json or {})
    mem_json["display_overview"] = {"title": title, "content": content}
    memory.memory_json = mem_json
    flag_modified(memory, "memory_json")
    memory.version = int(memory.version) + 1
    memory.last_source_type = "manual"
    memory.last_source_id = "travel_memory_overview"
    memory.updated_at = utcnow()
    session.add(memory)
    session.flush()
    session.add(
        UserMemoryEvent(
            id=id_service.new_memory_event_id(),
            user_id=memory.user_id,
            memory_id=memory.id,
            source_type="manual",
            source_id="travel_memory_overview",
            delta_json={
                "overview_title": title,
                "overview_content": content,
                "evidence_summary": "用户在旅行记忆抽屉中直接编辑概述",
            },
            version_after=memory.version,
        )
    )
    session.commit()
    display = adapter.to_display(memory)
    return success(display.model_dump(by_alias=True))


@router.put("/memories/travel/planning-preferences")
def update_travel_memory_planning_preferences(
    payload: MemoryPlanningPreferencesUpdateRequest,
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    memory = get_or_create_current_memory(session, current_user_id)
    mem_json = dict(memory.memory_json or {})
    planning_preferences = {
        "transport": payload.transport.strip(),
        "hotel": payload.hotel.strip(),
        "attractions": payload.attractions.strip(),
        "food": payload.food.strip(),
        "pace": payload.pace.strip(),
        "other": payload.other.strip(),
    }
    mem_json["planning_preferences"] = planning_preferences
    memory.memory_json = mem_json
    flag_modified(memory, "memory_json")
    preferences = mem_json.get("preferences", [])
    memory.memory_text = _render_memory_text(
        preferences if isinstance(preferences, list) else [],
        planning_preferences,
    )
    memory.version = int(memory.version) + 1
    memory.last_source_type = "manual"
    memory.last_source_id = "travel_memory_planning_preferences"
    memory.updated_at = utcnow()
    session.add(memory)
    session.flush()
    session.add(
        UserMemoryEvent(
            id=id_service.new_memory_event_id(),
            user_id=memory.user_id,
            memory_id=memory.id,
            source_type="manual",
            source_id="travel_memory_planning_preferences",
            delta_json={
                "planning_preferences": planning_preferences,
                "evidence_summary": "用户在旅行记忆抽屉中补充给规划直接使用的特别偏好",
            },
            version_after=memory.version,
        )
    )
    session.commit()
    display = adapter.to_display(memory)
    return success(display.model_dump(by_alias=True))


@router.delete("/memories/travel/descriptions/{description_id}")
def delete_travel_memory_description(
    description_id: str,
    current_user_id: str = Depends(get_current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    current_memory = get_or_create_current_memory(session, current_user_id)
    previous_summaries = adapter.source_text_for_description(current_memory, description_id)
    memory = _forget_memory_summaries(session, current_memory, previous_summaries, description_id)
    session.commit()
    display = adapter.to_display(memory)
    return success(display.model_dump(by_alias=True))
