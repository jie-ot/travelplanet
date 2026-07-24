"""Unique external-fact aggregation entry (《外部事实源与工具调用规范》五).

Only this service (called by `planning_service`) may invoke whitelisted tools.
The initial planning turn no longer pre-injects B-class entry guides; B-class
fallback rules live in the planning prompt and A′ tool failures still return a
structured official entry. Function Calling facts are executed here through
`execute_tool`, validated, logged, and returned to the model as compact JSON.
"""

from __future__ import annotations

import logging
import time

from pydantic import ValidationError

from app.ai.tools import (
    amap_provider,
    entry_guides,
    rail_mcp_provider,
    tool_specs,
)
from app.ai.tools.schemas import (
    BookingEvidence,
    PoiFact,
    RailFact,
    RouteFact,
    TravelFactPack,
    WeatherFact,
)
from app.core.business_logging import log_event
from app.db.session import session_scope
from app.models.base import utcnow
from app.models.itinerary import ItineraryData
from app.models.tool_call_log import ToolCallLog
from app.services import id_service

logger = logging.getLogger("travelplanet")


def needs_facts(message: str, context: ItineraryData | None) -> bool:
    """Whether planning should build an initial controlled fact pack.

    A/A′ facts are fetched through Function Calling. Returning false prevents
    ready-made B-class guidance from anchoring the model before it tries the
    relevant tools.
    """
    return False


def build_fact_pack(
    *,
    user_id: str,
    message: str,
    context: ItineraryData | None,
    task_type: str = "planning",
    request_id: str | None = None,
) -> TravelFactPack:
    """Build an empty compatibility pack; initial B-class prefill is disabled."""
    request_id = request_id or id_service.new_id("toolreq_")
    pack = TravelFactPack(request_id=request_id, generated_at=utcnow().isoformat())

    log_event(
        "planning_fact_pack_decision",
        status="info",
        request_id=request_id,
        prefetch_scope="none",
        inference="disabled",
        message_chars=len(message or ""),
        has_context=context is not None,
        routes_prefetched=0,
        weather_prefetched=0,
        pois_prefetched=0,
        rails_prefetched=0,
    )
    return pack

def _persist_log(
    user_id: str,
    request_id: str,
    task_type: str,
    *,
    tool_name: str,
    provider: str,
    status: str,
    degraded_to_b: bool,
    latency_ms: int | None,
    input_summary: dict | None,
    output_summary: dict | None,
    error_code: str | None,
) -> None:
    """Persist a single desensitized tool_call_logs row (non-fatal on failure)."""
    normalized_status = _log_status(status)
    log_event(
        "tool_call",
        status=normalized_status,
        request_id=request_id,
        task_type=task_type,
        tool_name=tool_name,
        provider=provider,
        fact_status=status,
        normalized_status=normalized_status,
        degraded_to_b=degraded_to_b,
        latency_ms=latency_ms,
        error_code=error_code,
        input_summary=_diagnostic_value(input_summary),
        output_summary=_diagnostic_value(output_summary),
        output_keys=list(output_summary) if isinstance(output_summary, dict) else [],
        has_error=normalized_status in {"failed", "timeout"},
    )
    try:
        with session_scope() as session:
            session.add(
                ToolCallLog(
                    id=id_service.new_tool_call_log_id(),
                    user_id=user_id,
                    request_id=request_id,
                    task_type=task_type,
                    tool_name=tool_name,
                    provider=provider,
                    input_summary=input_summary,
                    output_summary=output_summary,
                    status="fallback" if degraded_to_b else normalized_status,
                    degraded_to_b=degraded_to_b,
                    latency_ms=latency_ms,
                    error_code=error_code,
                )
            )
    except Exception:  # noqa: BLE001
        logger.exception("failed to write tool_call_log (non-fatal)")


def _log_status(fact_status: str) -> str:
    """Map a FactStatus to the tool_call_logs status enum (success/timeout/failed)."""
    if fact_status == "ok":
        return "success"
    if fact_status == "timeout":
        return "timeout"
    if fact_status == "needs_official_confirmation":
        # B-class template generated successfully (degrade handled by caller).
        return "success"
    return "failed"


# ============================================================
# Function-calling executor (《外部事实源与工具调用规范》五.4、任务详细流程规范十四)
# ============================================================
#
# The model (via the orchestrator's intercepted function calls) proposes which
# whitelist tool to run; this is the ONLY entry that executes those calls. It
# validates arguments against the per-tool Pydantic schema, applies config
# gating + the A′→B degrade rule, writes a desensitized tool_call_logs row, and
# returns a compact JSON-serializable result the model can read. It NEVER
# raises — any problem becomes a structured result so a single tool hiccup can
# never fail `/api/ai/planning`.


def execute_tool(
    *,
    user_id: str,
    request_id: str,
    task_type: str,
    tool_name: str,
    arguments: dict,
) -> dict:
    """Validate + execute one model-proposed whitelist tool; return a result dict."""
    schema = tool_specs.ARG_SCHEMAS.get(tool_name)
    if schema is None:
        _persist_log(
            user_id,
            request_id,
            task_type,
            tool_name=tool_name,
            provider="unknown",
            status="failed",
            degraded_to_b=False,
            latency_ms=None,
            input_summary={"arguments": _diagnostic_value(arguments)},
            output_summary={
                "reason": "tool_not_whitelisted",
                "allowed_tools": sorted(tool_specs.ARG_SCHEMAS),
            },
            error_code="tool_not_whitelisted",
        )
        return {"tool": tool_name, "status": "error", "message": "未知或未授权的工具"}

    try:
        args = schema.model_validate(arguments or {})
    except ValidationError as exc:
        _persist_log(
            user_id,
            request_id,
            task_type,
            tool_name=tool_name,
            provider="unknown",
            status="failed",
            degraded_to_b=False,
            latency_ms=None,
            input_summary={"arguments": _diagnostic_value(arguments)},
            output_summary={
                "reason": "invalid_args",
                "validation_errors": _diagnostic_value(exc.errors()),
            },
            error_code="invalid_args",
        )
        return {
            "tool": tool_name,
            "status": "error",
            "message": f"参数不合法：{exc.errors()[:1]}",
        }

    try:
        if tool_name == tool_specs.TOOL_AMAP_WEATHER_RANGE:
            return _exec_amap_weather_range(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_AMAP_POI_SEARCH:
            return _exec_amap_poi(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_AMAP_POI_AROUND:
            return _exec_amap_poi_around(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_AMAP_ROUTE:
            return _exec_amap_route(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_QUERY_RAIL:
            return _exec_rail(user_id, request_id, task_type, args)
    except Exception as exc:  # noqa: BLE001
        logger.exception("execute_tool failed (tool=%s, non-fatal)", tool_name)
        _persist_log(
            user_id,
            request_id,
            task_type,
            tool_name=tool_name,
            provider="unknown",
            status="failed",
            degraded_to_b=False,
            latency_ms=None,
            input_summary={
                "validated_arguments": _diagnostic_value(
                    args.model_dump(by_alias=True)
                )
            },
            output_summary={
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:2000],
            },
            error_code="exec_error",
        )
        return {
            "tool": tool_name,
            "status": "error",
            "message": "工具执行失败，请以官方渠道为准",
        }

    return {"tool": tool_name, "status": "error", "message": "未知或未授权的工具"}


def _diagnostic_value(value, *, depth: int = 0):  # noqa: ANN001, ANN202
    """Bound persisted/logged payload size while retaining useful structure."""
    if depth >= 5:
        return "<max-depth>"
    if isinstance(value, dict):
        items = list(value.items())
        result = {
            str(key): _diagnostic_value(item, depth=depth + 1)
            for key, item in items[:30]
        }
        if len(items) > 30:
            result["_omitted_key_count"] = len(items) - 30
        return result
    if isinstance(value, (list, tuple)):
        result = [_diagnostic_value(item, depth=depth + 1) for item in value[:20]]
        if len(value) > 20:
            result.append({"_omitted_item_count": len(value) - 20})
        return result
    if isinstance(value, str):
        return value if len(value) <= 1600 else value[:1600] + "…"
    return value


def _exec_amap_weather_range(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    if amap_provider.is_available():
        facts = amap_provider.weather_range(args.city, args.start_date, args.end_date)
        provider = "amap"
    else:
        facts = [
            WeatherFact(
                city=args.city,
                date=args.start_date,
                summary=None,
                status="provider_not_connected",
            )
        ]
        provider = "amap"
    status = _facts_status(facts)
    _persist_log(
        user_id,
        request_id,
        task_type,
        tool_name=tool_specs.TOOL_AMAP_WEATHER_RANGE,
        provider=provider,
        status=status,
        degraded_to_b=False,
        latency_ms=int((time.monotonic() - started) * 1000),
        input_summary={
            "city": args.city,
            "start_date": args.start_date,
            "end_date": args.end_date,
        },
        output_summary={"days": [_weather_summary(fact) for fact in facts]},
        error_code=None,
    )
    result = {
        "tool": tool_specs.TOOL_AMAP_WEATHER_RANGE,
        "status": status,
        "city": args.city,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "days": [_weather_summary(fact) for fact in facts],
    }
    if status != "ok":
        result["note"] = "部分或全部日期未获取到天气，建议以官方天气预报为准"
    return result


def _exec_amap_poi(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    if amap_provider.is_available():
        facts = amap_provider.poi_search_many(args.keyword, city=args.city, limit=8)
        provider = "amap"
    else:
        facts = [
            PoiFact(
                name=args.keyword,
                address=None,
                location=None,
                category=None,
                status="provider_not_connected",
            )
        ]
        provider = "amap"
    status = _facts_status(facts)
    _persist_log(
        user_id,
        request_id,
        task_type,
        tool_name=tool_specs.TOOL_AMAP_POI_SEARCH,
        provider=provider,
        status=status,
        degraded_to_b=False,
        latency_ms=int((time.monotonic() - started) * 1000),
        input_summary={"keyword": args.keyword, "city": args.city},
        output_summary={"pois": _poi_list_summary(facts)},
        error_code=None,
    )
    result = {
        "tool": tool_specs.TOOL_AMAP_POI_SEARCH,
        "status": status,
        "pois": _poi_list_summary(facts[:8]),
    }
    if status != "ok":
        result["note"] = "未获取到该地点信息，建议以官方/地图实际为准"
    return result


def _exec_amap_poi_around(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    city_hint = args.city or amap_provider.infer_city_hint(args.location)
    if amap_provider.is_available():
        facts = amap_provider.poi_around(
            args.location,
            args.keywords,
            radius=args.radius,
            city=city_hint,
            limit=8,
        )
        provider = "amap"
    else:
        facts = [
            PoiFact(
                name=args.keywords,
                address=None,
                location=args.location,
                category=None,
                status="provider_not_connected",
            )
        ]
        provider = "amap"
    status = _facts_status(facts)
    _persist_log(
        user_id,
        request_id,
        task_type,
        tool_name=tool_specs.TOOL_AMAP_POI_AROUND,
        provider=provider,
        status=status,
        degraded_to_b=False,
        latency_ms=int((time.monotonic() - started) * 1000),
        input_summary={
            "location": args.location,
            "keywords": args.keywords,
            "radius": args.radius,
            "city": city_hint,
        },
        output_summary={"pois": _poi_list_summary(facts)},
        error_code=None,
    )
    result = {
        "tool": tool_specs.TOOL_AMAP_POI_AROUND,
        "status": status,
        "pois": _poi_list_summary(facts[:8]),
    }
    if status != "ok":
        result["note"] = "未获取到周边 POI，建议以地图/官方平台实际为准"
    return result


def _exec_amap_route(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    origin_name = args.origin or args.origin_location
    destination_name = args.destination or args.destination_location
    city_hint = args.city or amap_provider.infer_city_hint(
        origin_name, destination_name
    )
    if amap_provider.is_available():
        origin_geo = (
            None
            if args.origin_location
            else amap_provider.geocode_detail(origin_name, city=city_hint)
        )
        dest_geo = (
            None
            if args.destination_location
            else amap_provider.geocode_detail(destination_name, city=city_hint)
        )
        origin_location = args.origin_location or (
            origin_geo.location if origin_geo else None
        )
        destination_location = args.destination_location or (
            dest_geo.location if dest_geo else None
        )
        if origin_location and destination_location:
            route_city = city_hint or (origin_geo.adcode if origin_geo else None) or (
                origin_geo.city if origin_geo else None
            )
            fact = amap_provider.route(
                origin_name,
                destination_name,
                origin_location,
                destination_location,
                mode=args.mode,
                city=route_city,
            )
        else:
            fact = RouteFact(
                origin=origin_name,
                destination=destination_name,
                mode=args.mode,
                distance_km=None,
                duration_minutes=None,
                status="unknown",
            )
        provider = "amap"
    else:
        fact = RouteFact(
            origin=origin_name,
            destination=destination_name,
            mode=args.mode,
            distance_km=None,
            duration_minutes=None,
            status="provider_not_connected",
        )
        provider = "amap"
    _persist_log(
        user_id,
        request_id,
        task_type,
        tool_name=tool_specs.TOOL_AMAP_ROUTE,
        provider=provider,
        status=fact.status,
        degraded_to_b=False,
        latency_ms=int((time.monotonic() - started) * 1000),
        input_summary={
            "origin": origin_name,
            "destination": destination_name,
            "origin_location": args.origin_location,
            "destination_location": args.destination_location,
            "mode": args.mode,
            "city": city_hint,
        },
        output_summary=_route_summary(fact),
        error_code=None,
    )
    result = {
        "tool": tool_specs.TOOL_AMAP_ROUTE,
        "status": fact.status,
        "origin": fact.origin,
        "destination": fact.destination,
        "mode": fact.mode,
        "distance_km": fact.distance_km,
        "duration_minutes": fact.duration_minutes,
    }
    if fact.status != "ok":
        result["note"] = "未获取到路线，建议以地图实际导航为准"
    return result


def _booking_summary(guide: BookingEvidence) -> dict:
    return {
        "status": guide.status,
        "booking_type": guide.booking_type,
        "official_channel": guide.official_channel,
        "query_hint": guide.query_hint,
        "notes": guide.notes,
    }


def _weather_summary(fact: WeatherFact) -> dict:
    return {
        "status": fact.status,
        "city": fact.city,
        "date": fact.date,
        "summary": fact.summary,
    }


def _poi_summary(fact: PoiFact) -> dict:
    return {
        "status": fact.status,
        "name": fact.name,
        "address": fact.address,
        "location": fact.location,
        "category": fact.category,
    }


def _poi_list_summary(facts: list[PoiFact]) -> list[dict]:
    return [_poi_summary(fact) for fact in facts]


def _facts_status(facts) -> str:  # noqa: ANN001
    if any(getattr(fact, "status", None) == "ok" for fact in facts):
        return "ok"
    first = next((getattr(fact, "status", None) for fact in facts), None)
    return first or "unknown"


def _route_summary(fact: RouteFact) -> dict:
    return {
        "status": fact.status,
        "origin": fact.origin,
        "destination": fact.destination,
        "mode": fact.mode,
        "distance_km": fact.distance_km,
        "duration_minutes": fact.duration_minutes,
    }


def _rail_summary(fact: RailFact) -> dict:
    return {
        "status": fact.status,
        "origin": fact.origin,
        "destination": fact.destination,
        "date": fact.date,
        "train_no": fact.train_no,
        "depart_time": fact.depart_time,
        "arrive_time": fact.arrive_time,
        "duration": fact.duration,
        "seat_class": fact.seat_class,
        "ref_price": fact.ref_price,
        "source": fact.source,
    }


def _rail_list_summary(facts: list[RailFact]) -> list[dict]:
    return [_rail_summary(fact) for fact in facts]


def _guide_dict(guide: BookingEvidence) -> dict:
    return {
        "booking_type": guide.booking_type,
        "official_channel": guide.official_channel,
        "query_hint": guide.query_hint,
        "notes": guide.notes,
    }


def _exec_rail(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    guide = entry_guides.rail_entry_guide(
        origin=args.origin, destination=args.destination, date=args.date
    )
    facts: list[RailFact] = []
    if rail_mcp_provider.is_enabled():
        facts = rail_mcp_provider.query_rail_options_sync(
            args.origin, args.destination, args.date, max_options=16
        )
    latency = int((time.monotonic() - started) * 1000)
    if facts:
        _persist_log(
            user_id,
            request_id,
            task_type,
            tool_name="rail_query_mcp",
            provider="rail_query_mcp",
            status="ok",
            degraded_to_b=False,
            latency_ms=latency,
            input_summary={
                "origin": args.origin,
                "destination": args.destination,
                "date": args.date,
            },
            output_summary={"candidates": _rail_list_summary(facts)},
            error_code=None,
        )
        recommended = facts[0]
        return {
            "tool": tool_specs.TOOL_QUERY_RAIL,
            "status": "ok",
            "reference": True,
            "origin": recommended.origin,
            "destination": recommended.destination,
            "date": recommended.date,
            "recommended": _rail_summary(recommended),
            "candidates": _rail_list_summary(facts),
            "source": "community_mcp",
            "disclaimer": "参考级数据，以 12306 官方实时为准，票价/余票请在 12306 官方渠道确认",
            "official_entry": _guide_dict(guide),
        }
    attempted = rail_mcp_provider.is_enabled()
    runtime_status = rail_mcp_provider.runtime_status()
    error_code = {
        "absent": "rail_mcp_not_started",
        "starting": "rail_mcp_warming_up",
        "failed": "rail_mcp_startup_or_call_failed",
        "invalid": "rail_mcp_invalid_endpoint",
        "ready": "rail_mcp_empty_or_invalid_result",
    }.get(runtime_status, "rail_mcp_unavailable" if attempted else None)
    _persist_log(
        user_id,
        request_id,
        task_type,
        tool_name=entry_guides.TOOL_RAIL,
        provider="rail_query_mcp" if attempted else entry_guides.PROVIDER_RAIL,
        status="needs_official_confirmation",
        degraded_to_b=attempted,
        latency_ms=latency,
        input_summary={
            "origin": args.origin,
            "destination": args.destination,
            "date": args.date,
        },
        output_summary=_booking_summary(guide),
        error_code=error_code,
    )
    return {
        "tool": tool_specs.TOOL_QUERY_RAIL,
        "status": "needs_official_confirmation",
        "note": "暂未获取到参考车次（社区 MCP 可能正在预热或不可用），请在 12306 官方 App 查询车次/席别/时刻并尽早购票或候补",
        "official_entry": _guide_dict(guide),
    }
