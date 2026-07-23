"""Implicit-traits (隐性特质) memory read/create/merge.

Master definition: 《任务详细流程规范》十、十三 与《数据库表结构与迁移规范》十.
`memory_json` is the structured source of truth; `memory_text` is a rendered
summary. The model only proposes increments — final merge is deterministic
here (confidence clamped 0–1, capped step per update, version += 1, write a
`user_memory_events` row). Never let the model overwrite the whole document;
never log the full document.

These functions take a caller-provided `Session` and do not commit; memory
updates run in their own transaction so a memory failure never rolls back an
already-saved business record.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.models.base import utcnow
from app.models.user_memory import UserMemory
from app.models.user_memory_event import UserMemoryEvent
from app.services import id_service

logger = logging.getLogger("travelplanet")

INITIAL_MEMORY_TEXT = "暂无稳定偏好，需从后续旅行照片和规划中逐步学习。"

# Per-update confidence step cap and new-preference seed cap so a single model
# output cannot reshape the whole profile (《任务详细流程规范》十三).
CONFIDENCE_STEP = 0.15
NEW_PREFERENCE_CONFIDENCE_CAP = 0.5
WEAKENED_THRESHOLD = 0.2

VALID_SOURCE_TYPES = {"generate", "plan_save", "plan_update", "manual"}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize_key(text: str) -> str:
    return text.strip().lower()


def get_or_create_current_memory(session: Session, user_id: str) -> UserMemory:
    """Fetch the user's current memory document, creating a blank one if absent."""
    memory = session.exec(
        select(UserMemory).where(UserMemory.user_id == user_id)
    ).first()
    if memory is not None:
        return memory

    memory = UserMemory(
        id=id_service.new_memory_id(),
        user_id=user_id,
        memory_text=INITIAL_MEMORY_TEXT,
        memory_json={"preferences": []},
        version=1,
    )
    session.add(memory)
    session.flush()
    return memory


def _render_memory_text(preferences: list[dict]) -> str:
    active = [p for p in preferences if p.get("status") != "weakened"]
    if not active:
        return INITIAL_MEMORY_TEXT
    lines = ["稳定旅行偏好："]
    for pref in sorted(active, key=lambda p: p.get("confidence", 0), reverse=True):
        conf = pref.get("confidence", 0)
        lines.append(f"- {pref.get('summary', pref.get('key', ''))}（置信度 {conf:.2f}）")
    return "\n".join(lines)


def merge_memory_update(
    session: Session,
    *,
    user_id: str,
    add_preferences: list[str],
    weaken_preferences: list[str],
    evidence_summary: str,
    confidence: float,
    source_type: str,
    source_id: str | None,
) -> UserMemory:
    """Deterministically merge a model-proposed increment into the document.

    Returns the updated `UserMemory`. The model never overwrites; we only
    add/strengthen or weaken individual preferences within capped steps.
    """
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(f"invalid source_type: {source_type}")

    memory = get_or_create_current_memory(session, user_id)
    mem_json = dict(memory.memory_json or {})
    preferences: list[dict] = list(mem_json.get("preferences", []))
    by_key = {p.get("key"): p for p in preferences}
    base_conf = _clamp(confidence)
    now_iso = utcnow().isoformat()

    for raw in add_preferences:
        if not raw or not raw.strip():
            continue
        key = _normalize_key(raw)
        existing = by_key.get(key)
        if existing is not None:
            existing["confidence"] = _clamp(existing.get("confidence", 0.0) + CONFIDENCE_STEP)
            existing["evidence_count"] = int(existing.get("evidence_count", 0)) + 1
            existing["last_seen"] = now_iso
            existing["status"] = "active"
            refs = list(existing.get("source_refs", []))
            if source_id and source_id not in refs:
                refs.append(source_id)
            existing["source_refs"] = refs
        else:
            new_pref = {
                "key": key,
                "category": None,
                "summary": raw.strip(),
                "confidence": _clamp(min(base_conf, NEW_PREFERENCE_CONFIDENCE_CAP)),
                "evidence_count": 1,
                "last_seen": now_iso,
                "source_refs": [source_id] if source_id else [],
                "status": "active",
            }
            preferences.append(new_pref)
            by_key[key] = new_pref

    for raw in weaken_preferences:
        if not raw or not raw.strip():
            continue
        key = _normalize_key(raw)
        existing = by_key.get(key)
        if existing is not None:
            existing["confidence"] = _clamp(existing.get("confidence", 0.0) - CONFIDENCE_STEP)
            existing["last_seen"] = now_iso
            if existing["confidence"] <= WEAKENED_THRESHOLD:
                existing["status"] = "weakened"

    mem_json["preferences"] = preferences
    memory.memory_json = mem_json
    memory.memory_text = _render_memory_text(preferences)
    memory.version = int(memory.version) + 1
    memory.last_source_type = source_type
    memory.last_source_id = source_id
    memory.updated_at = utcnow()
    session.add(memory)
    session.flush()

    event = UserMemoryEvent(
        id=id_service.new_memory_event_id(),
        user_id=user_id,
        memory_id=memory.id,
        source_type=source_type,
        source_id=source_id,
        delta_json={
            "add_preferences": add_preferences,
            "weaken_preferences": weaken_preferences,
            "evidence_summary": evidence_summary,
            "confidence": base_conf,
        },
        version_after=memory.version,
    )
    session.add(event)
    session.flush()
    return memory


def build_memory_summary(memory: UserMemory, max_chars: int = 1000) -> str:
    """Return a bounded summary for prompt injection (never the full doc raw)."""
    text = memory.memory_text or INITIAL_MEMORY_TEXT
    if len(text) > max_chars:
        return text[:max_chars]
    return text
