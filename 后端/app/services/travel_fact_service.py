"""Unique external-fact aggregation entry (《外部事实源与工具调用规范》五).

Only this service (called by `planning_service`) may invoke whitelisted tools.
The initial planning turn no longer pre-injects B-class entry guides; B-class
fallback rules live in the planning prompt and A′ tool failures still return a
structured official entry. Function Calling facts are executed here through
`execute_tool`, validated, logged, and returned to the model as compact JSON.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import logging
import re
import threading
import time

from pydantic import ValidationError

from app.ai.tools import (
    amap_provider,
    entry_guides,
    flight_text_parser,
    rail_mcp_provider,
    tool_specs,
    variflight_aviation_provider,
    variflight_tripmatch_provider,
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
_TOOL_LOG_WRITE_LOCK = threading.Lock()


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
        # External calls may finish concurrently. Serialize only the SQLite
        # transaction, not the network work, to avoid non-fatal "database locked"
        # losses in the audit trail.
        with _TOOL_LOG_WRITE_LOCK:
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
        return {
            "tool": tool_name,
            "status": "error",
            "message": "未知或未授权的工具",
            "error_code": "tool_not_whitelisted",
            "retryable": False,
        }

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
            "error_code": "invalid_args",
            "retryable": False,
        }

    try:
        if tool_name == tool_specs.TOOL_AMAP_WEATHER_RANGE:
            return _exec_amap_weather_range(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_AMAP_POI_SEARCH:
            return _exec_amap_poi(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_AMAP_POI_AROUND:
            return _exec_amap_poi_around(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_AMAP_POI_DETAIL:
            return _exec_amap_poi_detail(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_AMAP_ROUTE:
            return _exec_amap_route(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_SEARCH_FLIGHT_ITINERARIES:
            return _exec_flight_itineraries(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_SEARCH_FLIGHT_TRANSFER:
            return _exec_flight_transfer(user_id, request_id, task_type, args)
        if tool_name == tool_specs.TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER:
            return _exec_flight_train_transfer(
                user_id, request_id, task_type, args
            )
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
            "error_code": "exec_error",
            "error_type": type(exc).__name__,
            "retryable": _is_retryable_tool_exception(exc),
        }

    return {
        "tool": tool_name,
        "status": "error",
        "message": "未知或未授权的工具",
        "error_code": "tool_not_whitelisted",
        "retryable": False,
    }


def _is_retryable_tool_exception(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(
        marker in name or marker in text
        for marker in (
            "timeout",
            "connect",
            "connection",
            "ratelimit",
            "rate_limit",
            "temporar",
            "429",
            "502",
            "503",
            "504",
        )
    )


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


AMAP_FORECAST_HORIZON_DAYS = 3


def _beyond_forecast_horizon(start_date: str) -> bool:
    """Whether even the first requested day is past AMap's forecast horizon."""
    today = datetime.now(timezone(timedelta(hours=8))).date()
    try:
        start = date.fromisoformat(start_date)
    except (TypeError, ValueError):
        return False
    return (start - today).days >= AMAP_FORECAST_HORIZON_DAYS


def _exec_amap_weather_range(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    weather_query = args.adcode
    if _beyond_forecast_horizon(args.start_date):
        # AMap only publishes today plus the next two days. Calling it for a
        # trip weeks out burns budget and returns an all-unknown block that
        # earlier logged as a provider failure.
        result = {
            "tool": tool_specs.TOOL_AMAP_WEATHER_RANGE,
            "status": "out_of_forecast_horizon",
            "city": args.city,
            "adcode": weather_query,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "forecast_horizon_days": AMAP_FORECAST_HORIZON_DAYS,
            "days": [],
            "note": (
                f"{args.start_date} 距今超过高德 {AMAP_FORECAST_HORIZON_DAYS} 天预报范围，"
                "未发起查询。请勿用近日预报冒充，只能给出该地该季节的常规气候提示，"
                "并在 bookings/preparations 中提示出行前复核官方预报。"
            ),
            "retryable": False,
        }
        _persist_log(
            user_id,
            request_id,
            task_type,
            tool_name=tool_specs.TOOL_AMAP_WEATHER_RANGE,
            provider="amap",
            status="out_of_forecast_horizon",
            degraded_to_b=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_summary={
                "city": args.city,
                "adcode": weather_query,
                "start_date": args.start_date,
                "end_date": args.end_date,
                "skipped": True,
            },
            output_summary={"days": []},
            error_code=None,
        )
        return result
    if amap_provider.is_available():
        weather_query = weather_query or amap_provider.resolve_city_adcode(args.city)
        facts = amap_provider.weather_range(
            weather_query or args.city,
            args.start_date,
            args.end_date,
            display_city=args.city,
        )
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
            "adcode": weather_query,
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
        "adcode": weather_query,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "forecast_horizon_days": 3,
        "days": [_weather_summary(fact) for fact in facts],
    }
    if status != "ok":
        result["note"] = "部分或全部日期未获取到天气，建议以官方天气预报为准"
    return result


def _exec_amap_poi(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    if amap_provider.is_available():
        facts = amap_provider.poi_search_many(
            args.keyword,
            city=args.city,
            limit=args.limit,
            types=args.types,
            city_limit=args.city_limit,
        )
        provider = "amap"
    else:
        facts = [
            PoiFact(
                name=args.keyword or args.types or "POI",
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
        input_summary={
            "keyword": args.keyword,
            "types": args.types,
            "city": args.city,
            "city_limit": args.city_limit,
            "limit": args.limit,
        },
        output_summary={"pois": _poi_list_summary(facts)},
        error_code=None,
    )
    result = {
        "tool": tool_specs.TOOL_AMAP_POI_SEARCH,
        "status": status,
        "count": len(facts),
        "pois": _poi_list_summary(facts[: args.limit]),
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
            args.keyword,
            radius=args.radius,
            city=city_hint,
            limit=args.limit,
            types=args.types,
            city_limit=args.city_limit,
            sort_rule=args.sort_rule,
        )
        provider = "amap"
    else:
        facts = [
            PoiFact(
                name=args.keyword or args.types or "POI",
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
            "keyword": args.keyword,
            "types": args.types,
            "radius": args.radius,
            "city": city_hint,
            "city_limit": args.city_limit,
            "limit": args.limit,
            "sort_rule": args.sort_rule,
        },
        output_summary={"pois": _poi_list_summary(facts)},
        error_code=None,
    )
    result = {
        "tool": tool_specs.TOOL_AMAP_POI_AROUND,
        "status": status,
        "count": len(facts),
        "pois": _poi_list_summary(facts[: args.limit]),
    }
    if status != "ok":
        result["note"] = "未获取到周边 POI，建议以地图/官方平台实际为准"
    return result


def _exec_amap_poi_detail(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    if amap_provider.is_available():
        facts = amap_provider.poi_details(args.poi_ids)
    else:
        facts = [
            PoiFact(
                name=poi_id,
                address=None,
                location=None,
                category=None,
                status="provider_not_connected",
                poi_id=poi_id,
            )
            for poi_id in args.poi_ids
        ]
    status = _facts_status(facts)
    _persist_log(
        user_id,
        request_id,
        task_type,
        tool_name=tool_specs.TOOL_AMAP_POI_DETAIL,
        provider="amap",
        status=status,
        degraded_to_b=False,
        latency_ms=int((time.monotonic() - started) * 1000),
        input_summary={"poi_ids": args.poi_ids},
        output_summary={"pois": _poi_list_summary(facts)},
        error_code=None,
    )
    result = {
        "tool": tool_specs.TOOL_AMAP_POI_DETAIL,
        "status": status,
        "count": len(facts),
        "pois": _poi_list_summary(facts),
    }
    if status != "ok":
        result["note"] = "部分 POI 详情未获取到，建议以地图/景区官方信息为准"
    return result


def _exec_amap_route(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    origin_name = args.origin or args.origin_location
    destination_name = args.destination or args.destination_location
    city_hint = args.city or amap_provider.infer_city_hint(
        origin_name, destination_name
    )
    if amap_provider.is_available():
        # Both endpoints are independent prerequisites. Resolve them together;
        # the actual route request waits for both futures.
        with ThreadPoolExecutor(max_workers=2) as pool:
            origin_future = pool.submit(
                _resolve_route_endpoint,
                origin_name,
                args.origin_location,
                city_hint,
                args.mode,
                args.origin_adcode,
                args.origin_citycode,
            )
            destination_future = pool.submit(
                _resolve_route_endpoint,
                destination_name,
                args.destination_location,
                city_hint,
                args.mode,
                args.destination_adcode,
                args.destination_citycode,
            )
            origin_location, origin_adcode, origin_citycode = origin_future.result()
            destination_location, destination_adcode, destination_citycode = (
                destination_future.result()
            )
        if origin_location and destination_location:
            fact = amap_provider.route(
                origin_name,
                destination_name,
                origin_location,
                destination_location,
                mode=args.mode,
                origin_poi_id=args.origin_poi_id,
                destination_poi_id=args.destination_poi_id,
                origin_adcode=origin_adcode,
                destination_adcode=destination_adcode,
                origin_citycode=origin_citycode,
                destination_citycode=destination_citycode,
                strategy=args.strategy,
                alternatives=args.alternatives,
                waypoint_locations=args.waypoint_locations,
                destination_type=args.destination_type,
                vehicle_plate=args.vehicle_plate,
                car_type=args.car_type,
                avoid_ferry=args.avoid_ferry,
                night_service=args.night_service,
            )
        else:
            fact = RouteFact(
                origin=origin_name,
                destination=destination_name,
                mode=args.mode,
                distance_km=None,
                duration_minutes=None,
                status="unknown",
                origin_location=origin_location,
                destination_location=destination_location,
                origin_poi_id=args.origin_poi_id,
                destination_poi_id=args.destination_poi_id,
                strategy=args.strategy,
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
            "origin_poi_id": args.origin_poi_id,
            "destination_poi_id": args.destination_poi_id,
            "origin_adcode": args.origin_adcode,
            "destination_adcode": args.destination_adcode,
            "origin_citycode": args.origin_citycode,
            "destination_citycode": args.destination_citycode,
            "mode": args.mode,
            "city": city_hint,
            "strategy": args.strategy,
            "alternatives": args.alternatives,
            "waypoint_locations": args.waypoint_locations,
            "destination_type": args.destination_type,
            "vehicle_plate_supplied": bool(args.vehicle_plate),
            "car_type": args.car_type,
            "avoid_ferry": args.avoid_ferry,
            "night_service": args.night_service,
        },
        output_summary=_route_summary(fact),
        error_code=None,
    )
    result = {"tool": tool_specs.TOOL_AMAP_ROUTE, **_route_summary(fact)}
    if fact.status != "ok":
        result["note"] = "未获取到路线，建议以地图实际导航为准"
    return result


def _resolve_route_endpoint(
    name: str,
    location: str | None,
    city_hint: str | None,
    mode: str,
    supplied_adcode: str | None,
    supplied_citycode: str | None,
) -> tuple[str | None, str | None, str | None]:
    if location:
        normalized = amap_provider.normalize_coord(location)
        detail = None
        if mode == "transit" and not (supplied_adcode and supplied_citycode):
            detail = amap_provider.reverse_geocode_detail(normalized)
        return (
            normalized,
            supplied_adcode or (detail.adcode if detail else None),
            supplied_citycode or (detail.citycode if detail else None),
        )
    detail = amap_provider.geocode_detail(name, city=city_hint)
    if not detail:
        return None, supplied_adcode, supplied_citycode
    return (
        detail.location,
        supplied_adcode or detail.adcode,
        supplied_citycode or detail.citycode,
    )


def _booking_summary(guide: BookingEvidence) -> dict:
    return {
        "status": guide.status,
        "booking_type": guide.booking_type,
        "official_channel": guide.official_channel,
        "query_hint": guide.query_hint,
        "notes": guide.notes,
    }


def _weather_summary(fact: WeatherFact) -> dict:
    return fact.model_dump(exclude_none=True)


def _poi_summary(fact: PoiFact) -> dict:
    return fact.model_dump(exclude_none=True)


def _poi_list_summary(facts: list[PoiFact]) -> list[dict]:
    return [_poi_summary(fact) for fact in facts]


def _facts_status(facts) -> str:  # noqa: ANN001
    if any(getattr(fact, "status", None) == "ok" for fact in facts):
        return "ok"
    first = next((getattr(fact, "status", None) for fact in facts), None)
    return first or "unknown"


def _route_summary(fact: RouteFact) -> dict:
    return fact.model_dump(exclude_none=True)


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


def _tripmatch_output_summary(outcome) -> dict:  # noqa: ANN001
    payload = outcome.provider_payload
    upstream_request_id = payload.get("request_id") if isinstance(payload, dict) else None
    candidates = _flight_candidates(outcome)
    return {
        "provider_status": outcome.status,
        "payload_type": type(payload).__name__ if payload is not None else None,
        "payload_keys": list(payload)[:30] if isinstance(payload, dict) else [],
        "candidate_count": len(candidates),
        "structured_candidate_count": len(_tripmatch_candidates(payload)),
        "parsed_flight_nos": [
            str(item.get("flight_no")) for item in candidates if item.get("flight_no")
        ][:20],
        "reported_candidate_count": _reported_candidate_count(
            payload, outcome.raw_text
        ),
        "upstream_request_id": upstream_request_id,
        "raw_text_chars": len(outcome.raw_text or ""),
        "parsed_content_blocks": len(outcome.content or []),
        "error_code": outcome.error_code,
        "provider_trace_id": outcome.trace_id,
        "diagnostic_stage": outcome.diagnostic_stage,
        "http_status": outcome.http_status,
        "exception_type": outcome.exception_type,
        "provider_latency_ms": outcome.latency_ms,
    }


def _reported_candidate_count(payload, raw_text: str | None) -> int | None:  # noqa: ANN001
    text_parts = [raw_text or ""]
    if isinstance(payload, dict) and isinstance(payload.get("data"), str):
        text_parts.insert(0, payload["data"])
    for text in text_parts:
        match = re.search(r"查询到了\s*(\d+)\s*条", text)
        if match:
            return int(match.group(1))
    return None


def _flight_candidates(outcome) -> list[dict]:  # noqa: ANN001
    """Structure the upstream answer, whichever shape it arrives in.

    VariFlight currently answers in Chinese prose, so text parsing is tried
    first; the record-list walk stays as a fallback for a future structured
    response.
    """
    text = flight_text_parser.provider_text(outcome.provider_payload, outcome.raw_text)
    parsed = flight_text_parser.parse_flight_text(text)
    if parsed:
        return parsed
    return _tripmatch_candidates(outcome.provider_payload)


def _tripmatch_candidates(value, *, depth: int = 0) -> list[dict]:  # noqa: ANN001
    """Find the first record list without throwing away the original payload."""
    if depth > 5:
        return []
    if isinstance(value, list):
        records = [item for item in value if isinstance(item, dict)]
        if records:
            return records
        for item in value:
            found = _tripmatch_candidates(item, depth=depth + 1)
            if found:
                return found
        return []
    if not isinstance(value, dict):
        return []
    preferred_keys = (
        "flights",
        "flightList",
        "transferPlans",
        "transferInfos",
        "schemes",
        "plans",
        "candidates",
        "results",
        "items",
        "data",
        "result",
    )
    for key in preferred_keys:
        if key in value:
            found = _tripmatch_candidates(value[key], depth=depth + 1)
            if found:
                return found
    for child in value.values():
        found = _tripmatch_candidates(child, depth=depth + 1)
        if found:
            return found
    return []


def _tripmatch_result(  # noqa: ANN001
    tool_name: str,
    query: dict,
    outcome,
    *,
    provider: str = variflight_tripmatch_provider.PROVIDER,
    api_key_env: str = "VARIFLIGHT_API_KEY",
) -> dict:
    result = {
        "tool": tool_name,
        "status": outcome.status,
        "provider": provider,
        "query": query,
        "provider_payload": outcome.provider_payload,
        "provider_raw_text": outcome.raw_text,
        "provider_parsed_content": outcome.content,
    }
    candidates = _flight_candidates(outcome)
    if candidates:
        # Each independently selectable flight/transfer can receive its own
        # fact_id while the original provider payload remains lossless.
        result["candidates"] = candidates
    # `provider_payload`/`provider_raw_text` are stripped before the model sees
    # the fact, so the upstream answer must also survive in a bounded field.
    # Without this the model receives status=ok with no schedule and invents
    # departure times.
    text = flight_text_parser.provider_text(outcome.provider_payload, outcome.raw_text)
    if text:
        result["provider_summary_text"] = text
    result.update(flight_text_parser.parse_flight_summary(text))
    if candidates:
        result.update(flight_text_parser.departure_window(candidates))
    if outcome.error_code:
        result["error_code"] = outcome.error_code
    if outcome.error_message:
        result["error_message"] = outcome.error_message
    if outcome.http_status is not None:
        result["http_status"] = outcome.http_status
    result["retryable"] = _is_retryable_provider_result(
        status=outcome.status,
        error_code=outcome.error_code,
        http_status=outcome.http_status,
    )
    if outcome.status == "provider_not_connected":
        result["note"] = (
            "飞友 MCP 尚未配置可用 API Key，当前未执行真实查询；"
            f"填写 {api_key_env} 后生效"
        )
    elif outcome.status != "ok":
        result["note"] = "航班数据暂不可用，请以航司或机场官方渠道复核"
    return result


def _is_retryable_provider_result(
    *,
    status: str | None,
    error_code: str | None,
    http_status: int | None = None,
) -> bool:
    normalized_status = str(status or "").lower()
    normalized_code = str(error_code or "").lower()
    if normalized_status == "timeout" or http_status == 429:
        return True
    if isinstance(http_status, int) and http_status >= 500:
        return True
    return any(
        marker in normalized_code
        for marker in (
            "timeout",
            "rate_limited",
            "upstream_error",
            "connection",
            "temporar",
            "warming_up",
            "call_failed",
            "startup_or_call_failed",
        )
    )


def _exec_flight_itineraries(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    outcome = variflight_aviation_provider.search_flight_itineraries_sync(
        args.dep_city_code, args.dep_date, args.arr_city_code
    )
    latency = int((time.monotonic() - started) * 1000)
    query = {
        "depCityCode": args.dep_city_code,
        "depDate": args.dep_date,
        "arrCityCode": args.arr_city_code,
    }
    _persist_log(
        user_id,
        request_id,
        task_type,
        tool_name=tool_specs.TOOL_SEARCH_FLIGHT_ITINERARIES,
        provider=variflight_aviation_provider.PROVIDER,
        status=outcome.status,
        degraded_to_b=False,
        latency_ms=latency,
        input_summary={
            **query,
            "endpoint": variflight_aviation_provider.endpoint_identity(),
            "api_key_present": variflight_aviation_provider.is_configured(),
        },
        output_summary=_tripmatch_output_summary(outcome),
        error_code=outcome.error_code,
    )
    result = _tripmatch_result(
        tool_specs.TOOL_SEARCH_FLIGHT_ITINERARIES,
        query,
        outcome,
        provider=variflight_aviation_provider.PROVIDER,
    )
    result["verification"] = (
        "指定日期方案与价格均为查询时参考，出票前须在航司或正规售票平台复核"
    )
    return result


def _exec_flight_transfer(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    outcome = variflight_aviation_provider.search_flight_transfer_sync(
        args.depcity, args.arrcity, args.depdate
    )
    latency = int((time.monotonic() - started) * 1000)
    query = {
        "depcity": args.depcity,
        "arrcity": args.arrcity,
        "depdate": args.depdate,
    }
    _persist_log(
        user_id,
        request_id,
        task_type,
        tool_name=tool_specs.TOOL_SEARCH_FLIGHT_TRANSFER,
        provider=variflight_aviation_provider.PROVIDER,
        status=outcome.status,
        degraded_to_b=False,
        latency_ms=latency,
        input_summary={
            **query,
            "endpoint": variflight_aviation_provider.endpoint_identity(),
            "api_key_present": variflight_aviation_provider.is_configured(),
        },
        output_summary=_tripmatch_output_summary(outcome),
        error_code=outcome.error_code,
    )
    result = _tripmatch_result(
        tool_specs.TOOL_SEARCH_FLIGHT_TRANSFER,
        query,
        outcome,
        provider=variflight_aviation_provider.PROVIDER,
        api_key_env="VARIFLIGHT_API_KEY",
    )
    result["query_horizon"] = (
        "上游文档限定为从查询时点起至多未来 48 小时；超出窗口不得表述为已查询到"
    )
    result["transfer_type"] = "flight_to_flight"
    return result


def _exec_flight_train_transfer(user_id, request_id, task_type, args) -> dict:  # noqa: ANN001
    started = time.monotonic()
    outcome = variflight_tripmatch_provider.search_flight_train_transfer_sync(
        args.depcity, args.arrcity, args.depdate
    )
    latency = int((time.monotonic() - started) * 1000)
    query = {
        "depcity": args.depcity,
        "arrcity": args.arrcity,
        "depdate": args.depdate,
    }
    _persist_log(
        user_id,
        request_id,
        task_type,
        tool_name=tool_specs.TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER,
        provider=variflight_tripmatch_provider.PROVIDER,
        status=outcome.status,
        degraded_to_b=False,
        latency_ms=latency,
        input_summary={
            **query,
            "endpoint": variflight_tripmatch_provider.endpoint_identity(),
            "api_key_present": variflight_tripmatch_provider.is_configured(),
        },
        output_summary=_tripmatch_output_summary(outcome),
        error_code=outcome.error_code,
    )
    result = _tripmatch_result(
        tool_specs.TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER, query, outcome
    )
    result["rail_verification"] = {
        "required": True,
        "tool": tool_specs.TOOL_QUERY_RAIL,
        "instruction": (
            "Tripmatch 仅用于发现空铁中转候选；对每个入选铁路段，必须按实际起终点和日期"
            "另行调用 query_rail_tickets，由现有 12306 MCP 查询车次、时刻、票价和余票参考。"
        ),
    }
    return result


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
    query_error_code = (
        rail_mcp_provider.last_query_error_code() if attempted else None
    )
    error_code = query_error_code or {
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
        "error_code": error_code,
        "retryable": _is_retryable_provider_result(
            status="needs_official_confirmation",
            error_code=error_code,
        ),
    }
