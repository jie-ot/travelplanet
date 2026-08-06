"""In-process progress registry for the synchronous planning request.

`POST /ai/planning` stays a single blocking request (《后端技术栈与全局规范》八:
no SSE, no WebSocket). To let the client show the real backend state instead of a
cosmetic timer, the client generates a `progressToken`, sends it with the POST,
and polls `GET /ai/planning/progress` while the POST is in flight.

The token lives in a contextvar so nested orchestrator/tool code reports
progress without threading it through every signature, exactly like
``business_logging``. ``call_in_current_context`` already copies contextvars into
worker threads, so parallel tool calls report correctly too.

State is process-local and intentionally ephemeral: a lost snapshot only costs
the animation its detail, never the plan itself.
"""

from __future__ import annotations

import contextvars
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

_current_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "planning_progress_token",
    default=None,
)

MAX_TRACKED_SESSIONS = 64
SESSION_TTL_SECONDS = 30 * 60
MAX_RECENT_ACTIVITIES = 8
MAX_TOKEN_LENGTH = 64
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

# Each stage owns a percent band sized by how long it really takes, and the bar
# glides across that band as the stage runs. Two properties matter more than
# precision here: the bar must not stall (synthesis is a single model call of 2–4
# minutes that reports nothing in between), and it must not promise a finish it
# cannot keep — bands are therefore ceilings, never overshot.
#
# Durations are the wall clock of the 2026-08-05/06 五日双城 runs, minus the LLM
# audit (removed) and the wasted empty-content retries (fixed): research and the
# single synthesis call now dominate, and verification is deterministic.
_STAGE_SPAN: dict[str, tuple[float, float]] = {
    "queued": (1.0, 3.0),
    "reading_memory": (3.0, 5.0),
    "collecting_requirements": (5.0, 8.0),
    "prefetching_facts": (5.0, 8.0),
    "understanding_request": (8.0, 10.0),
    "researching": (10.0, 52.0),
    "research_complete": (52.0, 55.0),
    "selecting_facts": (55.0, 58.0),
    "synthesizing": (58.0, 88.0),
    "verifying": (88.0, 96.0),
    "finalizing": (96.0, 99.0),
    "completed": (100.0, 100.0),
}
_STAGE_EXPECTED_MS: dict[str, float] = {
    "reading_memory": 2_000,
    "collecting_requirements": 20_000,
    "prefetching_facts": 15_000,
    "understanding_request": 10_000,
    "researching": 200_000,
    "selecting_facts": 2_000,
    "synthesizing": 150_000,
    "verifying": 30_000,
    "finalizing": 10_000,
}
_STAGE_LABELS: dict[str, str] = {
    "queued": "正在准备规划任务",
    "reading_memory": "正在读取你的旅行偏好",
    "collecting_requirements": "正在梳理本次旅行需求",
    "prefetching_facts": "正在预取基础事实",
    "understanding_request": "正在理解本次旅行需求",
    "researching": "正在查询真实交通、住宿与景点事实",
    "research_complete": "事实查询完成，正在整理证据",
    "selecting_facts": "正在筛选行程要用到的事实",
    "synthesizing": "正在编排每日行程",
    "verifying": "正在核对时刻、距离与可执行性",
    "finalizing": "正在生成每日地图并做最后校验",
    "completed": "行程已生成",
    "failed": "规划未能完成",
}
# Observed wall time for a full multi-city plan; used only until enough progress
# has accumulated to extrapolate from the real pace.
_DEFAULT_TOTAL_MS = sum(_STAGE_EXPECTED_MS.values())
_MIN_TOTAL_MS = 60_000
_MAX_TOTAL_MS = 900_000


@dataclass
class _Session:
    token: str
    started: float = field(default_factory=time.monotonic)
    created_at: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.monotonic)
    stage_started: float = field(default_factory=time.monotonic)
    round_started: float = field(default_factory=time.monotonic)
    stage: str = "queued"
    phase: str = "preparing"
    percent: float = 1.0
    research_round: int = 0
    target_rounds: int = 0
    max_rounds: int = 0
    tool_call_count: int = 0
    fact_count: int = 0
    repair_round: int = 0
    planning_model: str | None = None
    detail: str | None = None
    error: str | None = None
    recent_activities: list[str] = field(default_factory=list)

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


_sessions: dict[str, _Session] = {}
_lock = threading.RLock()


def normalize_token(token: str | None) -> str | None:
    """Validate a bounded, opaque client token, or reject it entirely.

    Rejecting is deliberate: silently rewriting a malformed token would make the
    client poll one key while the request publishes under another, so the
    animation would hang on "preparing" with no way to tell why.
    """
    if not token:
        return None
    candidate = token.strip()
    if not candidate or len(candidate) > MAX_TOKEN_LENGTH:
        return None
    return candidate if _TOKEN_PATTERN.fullmatch(candidate) else None


@contextmanager
def progress_session(
    token: str | None,
    **fields: Any,
) -> Iterator[None]:
    """Track one planning request under a client-supplied token."""
    normalized = normalize_token(token)
    if normalized is None:
        yield
        return
    with _lock:
        _sessions[normalized] = _Session(token=normalized, **fields)
        _evict_locked()
    context_token = _current_token.set(normalized)
    try:
        yield
    except Exception as exc:
        fail(str(exc))
        raise
    finally:
        _current_token.reset(context_token)


def report(
    stage: str | None = None,
    *,
    activity: str | None = None,
    detail: str | None = None,
    **fields: Any,
) -> None:
    """Update the active session. A no-op when the client sent no token."""
    token = _current_token.get()
    if token is None:
        return
    with _lock:
        session = _sessions.get(token)
        if session is None:
            return
        now = time.monotonic()
        if stage is not None and stage != session.stage:
            session.stage = stage
            session.phase = _phase_for_stage(stage)
            session.stage_started = now
        previous_round = session.research_round
        for key, value in fields.items():
            if value is not None and hasattr(session, key):
                setattr(session, key, value)
        if session.research_round != previous_round:
            session.round_started = now
        if detail is not None:
            session.detail = detail
        if activity:
            session.recent_activities.append(activity)
            del session.recent_activities[:-MAX_RECENT_ACTIVITIES]
        session.percent = max(session.percent, _percent_for(session))
        session.updated = time.monotonic()


def add_tool_activity(activity: str, *, count: int = 1) -> None:
    """Record one executed external query so the UI shows real work."""
    token = _current_token.get()
    if token is None:
        return
    with _lock:
        session = _sessions.get(token)
        if session is None:
            return
        session.tool_call_count += count
        if activity:
            session.recent_activities.append(activity)
            del session.recent_activities[:-MAX_RECENT_ACTIVITIES]
        session.percent = max(session.percent, _percent_for(session))
        session.updated = time.monotonic()


def finish(**fields: Any) -> None:
    report("completed", **fields)


def fail(message: str) -> None:
    token = _current_token.get()
    if token is None:
        return
    with _lock:
        session = _sessions.get(token)
        if session is None:
            return
        session.stage = "failed"
        session.phase = "failed"
        session.error = message[:300]
        session.updated = time.monotonic()


def snapshot(token: str | None) -> dict[str, Any] | None:
    """Public view for the polling endpoint."""
    normalized = normalize_token(token)
    if normalized is None:
        return None
    with _lock:
        _evict_locked()
        session = _sessions.get(normalized)
        if session is None:
            return None
        return _snapshot_locked(session)


def _snapshot_locked(session: _Session) -> dict[str, Any]:
    elapsed_ms = session.elapsed_ms()
    # Recomputed per poll, not per report: synthesis is a single model call that
    # reports nothing for minutes, and a frozen bar reads as a hang.
    session.percent = max(session.percent, _percent_for(session))
    percent = round(session.percent, 1)
    estimated_total = _estimated_total_ms(elapsed_ms, session.percent, session.stage)
    return {
        "token": session.token,
        "stage": session.stage,
        "stageLabel": _STAGE_LABELS.get(session.stage, "正在生成行程"),
        "phase": session.phase,
        "percent": percent,
        "detail": session.detail,
        "elapsedMs": elapsed_ms,
        "estimatedTotalMs": estimated_total,
        "estimatedRemainingMs": max(0, estimated_total - elapsed_ms),
        "researchRound": session.research_round,
        "targetRounds": session.target_rounds,
        "maxRounds": session.max_rounds,
        "toolCallCount": session.tool_call_count,
        "factCount": session.fact_count,
        "repairRound": session.repair_round,
        "planningModel": session.planning_model,
        "recentActivities": list(session.recent_activities),
        "error": session.error,
        "done": session.stage in {"completed", "failed"},
    }


def _phase_for_stage(stage: str) -> str:
    if stage in {
        "queued",
        "reading_memory",
        "collecting_requirements",
        "prefetching_facts",
        "understanding_request",
    }:
        return "preparing"
    if stage in {"researching", "research_complete"}:
        return "researching"
    if stage in {"selecting_facts", "synthesizing"}:
        return "composing"
    if stage in {"verifying", "finalizing"}:
        return "verifying"
    if stage == "completed":
        return "completed"
    return "failed"


def _percent_for(session: _Session) -> float:
    """Where the bar should sit right now, capped by the current stage's band."""
    span = _STAGE_SPAN.get(session.stage)
    if span is None:
        return session.percent
    start, end = span
    if session.stage == "researching":
        return start + (end - start) * _research_ratio(session)
    expected = _STAGE_EXPECTED_MS.get(session.stage)
    if not expected:
        return start
    in_stage_ms = (time.monotonic() - session.stage_started) * 1000
    return start + (end - start) * min(1.0, in_stage_ms / expected)


def _research_ratio(session: _Session) -> float:
    """Rounds are the honest signal; time only fills the gap between them."""
    horizon = max(session.target_rounds, session.research_round, 1)
    completed = max(0, session.research_round - 1)
    expected_round_ms = _STAGE_EXPECTED_MS["researching"] / horizon
    in_round_ms = (time.monotonic() - session.round_started) * 1000
    within = min(1.0, in_round_ms / expected_round_ms) if expected_round_ms else 0.0
    return min(1.0, (completed + within) / horizon)


def _estimated_total_ms(elapsed_ms: int, percent: float, stage: str) -> int:
    if stage == "completed":
        return elapsed_ms
    if percent < 8 or elapsed_ms < 3_000:
        return max(_DEFAULT_TOTAL_MS, elapsed_ms)
    projected = int(elapsed_ms * 100 / percent)
    # The estimate must never promise a finish time already in the past.
    return max(_MIN_TOTAL_MS, min(_MAX_TOTAL_MS, max(projected, elapsed_ms + 5_000)))


def _evict_locked() -> None:
    now = time.time()
    stale = [
        token
        for token, session in _sessions.items()
        if now - session.created_at > SESSION_TTL_SECONDS
    ]
    for token in stale:
        _sessions.pop(token, None)
    if len(_sessions) <= MAX_TRACKED_SESSIONS:
        return
    oldest = sorted(_sessions.items(), key=lambda item: item[1].created_at)
    for token, _session in oldest[: len(_sessions) - MAX_TRACKED_SESSIONS]:
        _sessions.pop(token, None)


def reset_for_tests() -> None:
    with _lock:
        _sessions.clear()
