"""`/api/ai/planning` (#6) main flow (《任务详细流程规范》七).

Validate → read memory → (Phase 6: controlled fact pack) → orchestrator returns
full ItineraryData (one JSON-repair retry inside orchestrator) → deterministic
schedule ordering → stable-ID alignment vs context → business validation → return full ItineraryData. Read-
only: no DB transaction, NO profile update (planning never updates memory).
Failures: 1002 (param), 1001 (model/JSON/validation), 1003 (config).
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai import orchestrator
from app.core.business_logging import log_event, timed_stage
from app.core.exceptions import (
    AIGenerationError,
    BusinessError,
    InternalError,
    InvalidParamError,
)
from app.db.session import session_scope
from app.models.dto import PlanningRequest
from app.models.itinerary import ItineraryData
from app.services import (
    id_service,
    itinerary_id_service,
    itinerary_validation_service,
    memory_service,
    travel_fact_service,
)

logger = logging.getLogger("travelplanet")


def plan(user_id: str, request: PlanningRequest) -> ItineraryData:
    """Generate the next full ItineraryData for the planning conversation."""
    if not request.message or not request.message.strip():
        raise InvalidParamError("规划需求不能为空")

    with timed_stage("planning_read_memory"):
        with session_scope() as session:
            memory = memory_service.get_or_create_current_memory(session, user_id)
            memory_summary = memory_service.build_memory_summary(memory)

    try:
        # Do not pre-inject ready-made B-class guidance. A/A′ facts are obtained
        # through the Function Calling loop; the prompt supplies B-class fallback
        # rules for use only after a relevant tool fails or is unavailable.
        request_id = id_service.new_id("toolreq_")
        log_event(
            "planning_tool_request_created",
            request_id=request_id,
            task_type="planning",
            protocol="declare_scope_fact_state_finish_audit_v1",
            is_refinement=request.context is not None,
            message_chars=len(request.message),
            message_excerpt=_excerpt(request.message),
        )
        fact_pack_dict = None
        if travel_fact_service.needs_facts(request.message, request.context):
            with timed_stage("planning_build_fact_pack"):
                try:
                    pack = travel_fact_service.build_fact_pack(
                        user_id=user_id,
                        message=request.message,
                        context=request.context,
                        task_type="planning",
                        request_id=request_id,
                    )
                    fact_pack_dict = pack.model_dump()
                    log_event(
                        "planning_fact_pack_result",
                        status="success",
                        tool_calls=len(pack.tool_calls),
                        booking_evidences=len(pack.booking_evidences),
                        rails=len(pack.rails),
                        weather=len(pack.weather),
                        pois=len(pack.pois),
                        summary=_summarize_fact_pack(fact_pack_dict),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "fact pack build failed (non-fatal, planning continues)"
                    )
                    log_event(
                        "planning_fact_pack_result",
                        status="failed",
                        message=str(exc),
                        error_type=type(exc).__name__,
                    )
                    fact_pack_dict = None

        log_event(
            "planning_prompt_summary",
            status="ready",
            message_excerpt=_excerpt(request.message),
            message_chars=len(request.message),
            context=_summarize_context(request.context),
            memory_summary_chars=len(memory_summary),
            fact_pack=_summarize_fact_pack(fact_pack_dict),
            request_id=request_id,
            is_refinement=request.context is not None,
            protocol="declare_scope_fact_state_finish_audit_v1",
        )

        # Function-calling executor: the model proposes whitelist tools; the
        # orchestrator intercepts and routes each call here for validation +
        # execution. Never raises (tool issues become structured results).
        def _execute_tool(tool_name: str, arguments: dict) -> dict:
            return travel_fact_service.execute_tool(
                user_id=user_id,
                request_id=request_id,
                task_type="planning",
                tool_name=tool_name,
                arguments=arguments,
            )

        with timed_stage(
            "planning_model_generate",
            request_id=request_id,
            is_refinement=request.context is not None,
            protocol="declare_scope_fact_state_finish_audit_v1",
        ):
            new_data = orchestrator.plan_itinerary(
                message=request.message,
                context=request.context,
                memory_summary=memory_summary,
                fact_pack=fact_pack_dict,
                execute_tool=_execute_tool,
            )
        # Schedule order is deterministic, so correct model ordering mistakes
        # before positional stable-ID alignment and business validation.
        with timed_stage(
            "planning_align_and_validate",
            request_id=request_id,
            generated_destination=new_data.trip_info.destination,
            generated_day_count=len(new_data.itinerary),
            generated_schedule_count=sum(
                len(day.schedules) for day in new_data.itinerary
            ),
        ):
            reordered_dates = itinerary_validation_service.normalize_schedule_order(
                new_data
            )
            if reordered_dates:
                log_event(
                    "planning_schedule_order_normalized",
                    status="corrected",
                    dates=reordered_dates,
                )
            aligned = itinerary_id_service.align_ids(new_data, request.context)
            try:
                itinerary_validation_service.validate_itinerary(aligned)
            except InvalidParamError as exc:
                # For #6, business-rule failure maps to AI failure (1001).
                raise AIGenerationError(f"行程规划校验失败：{exc.message}") from exc
        log_event(
            "planning_result",
            status="success",
            destination=aligned.trip_info.destination,
            day_count=len(aligned.itinerary),
            request_id=request_id,
            structure=_summarize_itinerary(aligned),
        )
        return aligned
    except BusinessError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("planning failed")
        raise InternalError("行程规划服务内部错误") from exc


def _excerpt(text: str | None, limit: int = 240) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    return compact[:limit] + ("..." if len(compact) > limit else "")


def _summarize_context(context: ItineraryData | None) -> dict[str, Any] | None:
    if context is None:
        return None
    return {
        "destination": context.trip_info.destination,
        "start_date": context.trip_info.start_date,
        "end_date": context.trip_info.end_date,
        "date_label": context.trip_info.date_label,
        "day_count": len(context.itinerary),
        "days": [
            {
                "id": day.id,
                "date": day.date,
                "title": day.title,
                "schedule_count": len(day.schedules),
            }
            for day in context.itinerary
        ],
    }


def _summarize_fact_pack(fact_pack: dict[str, Any] | None) -> dict[str, Any] | None:
    if not fact_pack:
        return None
    return {
        "request_id": fact_pack.get("request_id"),
        "routes": _summarize_facts(fact_pack.get("routes") or []),
        "weather": _summarize_facts(fact_pack.get("weather") or []),
        "pois": _summarize_facts(fact_pack.get("pois") or []),
        "rails": _summarize_facts(fact_pack.get("rails") or []),
        "booking_evidences": [
            {
                "booking_type": item.get("booking_type"),
                "official_channel": item.get("official_channel"),
                "query_hint": item.get("query_hint"),
                "status": item.get("status"),
            }
            for item in (fact_pack.get("booking_evidences") or [])
        ],
        "tool_calls": [
            {
                "tool_name": item.get("tool_name"),
                "provider": item.get("provider"),
                "status": item.get("status"),
                "degraded_to_b": item.get("degraded_to_b"),
                "latency_ms": item.get("latency_ms"),
                "error_code": item.get("error_code"),
            }
            for item in (fact_pack.get("tool_calls") or [])
        ],
    }


def _summarize_facts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in items:
        summary.append(
            {
                key: item.get(key)
                for key in (
                    "status",
                    "city",
                    "date",
                    "summary",
                    "name",
                    "address",
                    "category",
                    "origin",
                    "destination",
                    "mode",
                    "distance_km",
                    "duration_minutes",
                    "train_no",
                    "depart_time",
                    "arrive_time",
                    "ref_price",
                )
                if item.get(key) is not None
            }
        )
    return summary


def _summarize_itinerary(data: ItineraryData) -> dict[str, Any]:
    schedule_dicts = [
        schedule.model_dump(by_alias=True)
        for day in data.itinerary
        for schedule in day.schedules
    ]
    fact_refs = [
        fact_id
        for schedule in schedule_dicts
        for fact_id in (schedule.get("fact_refs") or [])
    ]
    return {
        "trip_info": data.trip_info.model_dump(by_alias=True),
        "experience_summary": (
            data.experience_summary.model_dump(by_alias=True)
            if data.experience_summary is not None
            else None
        ),
        "preparation_count": len(data.preparations),
        "booking_count": len(data.bookings),
        "food_count": len(data.food_recommendations),
        "schedule_count": len(schedule_dicts),
        "fact_ref_count": len(fact_refs),
        "unique_fact_ref_count": len(set(fact_refs)),
        "fact_status_counts": _count_values(
            schedule.get("fact_status") for schedule in schedule_dicts
        ),
        "missing_location_count": sum(
            not schedule.get("location") for schedule in schedule_dicts
        ),
        "missing_route_evidence_count": sum(
            bool(schedule.get("travel_minutes"))
            and not any(
                "route" in fact_id for fact_id in (schedule.get("fact_refs") or [])
            )
            for schedule in schedule_dicts
        ),
        "days": [
            {
                "id": day.id,
                "date": day.date,
                "title": day.title,
                "schedule_count": len(day.schedules),
                "schedules": [
                    {
                        "id": schedule.id,
                        "time_period": schedule.time_period,
                        "start_time": schedule.start_time,
                        "end_time": schedule.end_time,
                        "activity": _excerpt(schedule.activity, 120),
                        "transport": schedule.transport,
                        **{
                            key: value
                            for key, value in schedule.model_dump(
                                by_alias=True
                            ).items()
                            if key
                            in {
                                "place_name",
                                "location",
                                "duration_minutes",
                                "travel_minutes",
                                "distance_km",
                                "transport_mode",
                                "tags",
                                "booking_required",
                                "fact_status",
                                "fact_refs",
                                "action",
                            }
                        },
                    }
                    for schedule in day.schedules
                ],
            }
            for day in data.itinerary
        ],
    }


def _count_values(values) -> dict[str, int]:  # noqa: ANN001
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unspecified")
        counts[key] = counts.get(key, 0) + 1
    return counts
