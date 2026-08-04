"""`/api/ai/planning` (#6) main flow (《任务详细流程规范》七).

Validate → read memory → multi-turn requirement intake → explicit user
confirmation → controlled fact research → full ItineraryData → deterministic
alignment and validation. Planning never updates the user's preference profile.
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
from app.models.dto import (
    PlanningChatMessage,
    PlanningRequest,
    PlanningResponse,
)
from app.models.itinerary import ItineraryData
from app.services import (
    id_service,
    itinerary_id_service,
    itinerary_validation_service,
    memory_service,
    planning_intake_service,
    travel_fact_service,
)

logger = logging.getLogger("travelplanet")


def _validate_conversation_model(request: PlanningRequest) -> None:
    """Reject attempts to switch a model inside the supplied chat history."""
    historical_models = {
        message.planning_model
        for message in request.messages
        if message.planning_model is not None
    }
    if not historical_models or historical_models == {request.planning_model}:
        return
    log_event(
        "planning_model_switch_blocked",
        status="rejected",
        requested_model=request.planning_model,
        historical_models=sorted(historical_models),
        conversation_turns=len(request.messages),
    )
    raise InvalidParamError("一次规划对话只能使用一个模型；请重新开始规划后再切换")


def plan(user_id: str, request: PlanningRequest) -> PlanningResponse:
    """Advance requirement chat or generate after explicit confirmation."""
    if not request.message or not request.message.strip():
        raise InvalidParamError("规划需求不能为空")
    _validate_conversation_model(request)

    with timed_stage("planning_read_memory"):
        with session_scope() as session:
            memory = memory_service.get_or_create_current_memory(session, user_id)
            memory_summary = memory_service.build_memory_summary(memory)

    try:
        if request.context is None and not request.confirmed:
            return _collect_requirements(
                request=request,
                memory_summary=memory_summary,
            )

        planning_message = request.message
        if request.context is None:
            if request.brief is None:
                raise InvalidParamError("确认生成前缺少旅行需求清单")
            brief = planning_intake_service.normalize_brief(request.brief)
            missing = planning_intake_service.missing_required_fields(brief)
            if missing:
                log_event(
                    "planning_confirmation_blocked",
                    status="collecting",
                    missing_fields=missing,
                )
                return PlanningResponse(
                    phase="collecting",
                    assistant_message=_missing_fields_message(missing),
                    planning_model=request.planning_model,
                    brief=brief,
                    checklist=planning_intake_service.build_checklist(brief),
                )
            planning_message = planning_intake_service.confirmed_requirement_text(
                brief,
                request.message,
            )
            log_event(
                "planning_confirmation_accepted",
                status="confirmed",
                destination_count=len(brief.destinations),
                start_date=brief.start_date,
                end_date=brief.end_date,
                planning_model=request.planning_model,
            )

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
            message_chars=len(planning_message),
            message_excerpt=_excerpt(planning_message),
            planning_model=request.planning_model,
        )
        fact_pack_dict = None
        if travel_fact_service.needs_facts(planning_message, request.context):
            with timed_stage("planning_build_fact_pack"):
                try:
                    pack = travel_fact_service.build_fact_pack(
                        user_id=user_id,
                        message=planning_message,
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
            message_excerpt=_excerpt(planning_message),
            message_chars=len(planning_message),
            context=_summarize_context(request.context),
            memory_summary_chars=len(memory_summary),
            fact_pack=_summarize_fact_pack(fact_pack_dict),
            request_id=request_id,
            is_refinement=request.context is not None,
            protocol="declare_scope_fact_state_finish_audit_v1",
            planning_model=request.planning_model,
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
            planning_model=request.planning_model,
        ):
            new_data = orchestrator.plan_itinerary(
                message=planning_message,
                context=request.context,
                memory_summary=memory_summary,
                fact_pack=fact_pack_dict,
                execute_tool=_execute_tool,
                planning_model=request.planning_model,
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
            planning_model=request.planning_model,
        )
        return PlanningResponse(
            phase="completed",
            assistant_message="行程已经生成完成，你还可以继续告诉我想怎么调整。",
            planning_model=request.planning_model,
            brief=request.brief,
            itinerary=aligned,
        )
    except BusinessError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("planning failed")
        raise InternalError("行程规划服务内部错误") from exc


def _collect_requirements(
    *,
    request: PlanningRequest,
    memory_summary: str,
) -> PlanningResponse:
    messages = list(request.messages)
    if not messages or (
        messages[-1].role != "user"
        or messages[-1].content.strip() != request.message.strip()
    ):
        messages.append(
            PlanningChatMessage(
                role="user",
                content=request.message,
                planning_model=request.planning_model,
            )
        )
    with timed_stage(
        "planning_collect_requirements",
        conversation_turns=len(messages),
        has_previous_brief=request.brief is not None,
        planning_model=request.planning_model,
    ):
        intake = orchestrator.collect_planning_requirements(
            messages=messages,
            previous_brief=request.brief,
            memory_summary=memory_summary,
            planning_model=request.planning_model,
        )
    brief = planning_intake_service.normalize_brief(intake.brief)
    missing = planning_intake_service.missing_required_fields(brief)
    phase = "collecting" if missing else "confirming"
    assistant_message = intake.assistant_message.strip()
    if not assistant_message:
        assistant_message = (
            _missing_fields_message(missing)
            if missing
            else "必要信息已经齐了。请核对确认清单；你可以继续补充，也可以确认生成。"
        )
    checklist = planning_intake_service.build_checklist(brief)
    log_event(
        "planning_intake_result",
        status=phase,
        conversation_turns=len(messages),
        missing_fields=missing,
        checklist_status_counts=_count_values(item.status for item in checklist),
        planning_model=request.planning_model,
    )
    return PlanningResponse(
        phase=phase,
        assistant_message=assistant_message,
        planning_model=request.planning_model,
        brief=brief,
        checklist=checklist,
    )


def _missing_fields_message(missing: list[str]) -> str:
    labels = {
        "origin": "出发地",
        "destinations": "目的地",
        "startDate": "开始日期",
        "endDate": "结束日期",
    }
    readable = "、".join(labels.get(field, field) for field in missing)
    return f"还差一点必要信息：{readable}。补充后我会先给你一份确认清单，不会直接生成行程。"


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
