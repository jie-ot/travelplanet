"""A′ flight community-MCP adapter (《外部事实源与工具调用规范》二.2、三·补充).

Wraps the flight community MCP server (`flight-ticket-mcp-server`, requires
Python ≥ 3.11) behind a synchronous `query_flight_sync(...)`. Probe + timeout +
exception → return None → B-class degradation. NEVER let A′ exceptions bubble.

The server's route/price data is reference-level (partly simulated/aggregated,
not airline-official real-time); `FlightFact` is always treated as
"参考、以航司官方为准". This project does NOT use its weather tools (weather is
A-class 高德/和风 only).
"""

from __future__ import annotations

import logging

from app.ai.tools.schemas import FlightFact
from app.core.config import settings

logger = logging.getLogger("travelplanet")


def is_enabled() -> bool:
    """A′ flight is attempted only when enabled with a configured endpoint."""
    return bool(
        settings.TOOLS_ENABLED
        and settings.FLIGHT_MCP_ENABLED
        and settings.FLIGHT_MCP_ENDPOINT
    )


def query_flight_sync(origin: str, destination: str, date: str) -> FlightFact | None:
    """Query reference flight facts. None on any issue → caller degrades B."""
    if not is_enabled():
        return None
    try:
        raw = _invoke_mcp(origin, destination, date)
    except Exception:  # noqa: BLE001
        logger.warning("A' flight MCP failed; degrading to B (no exception bubbled)")
        return None
    if not raw:
        return None
    try:
        return FlightFact(
            origin=origin,
            destination=destination,
            date=date,
            flight_no=raw.get("flight_no") or raw.get("flightNo") or raw.get("flight_number"),
            depart_time=raw.get("depart_time") or raw.get("departureTime") or raw.get("depTime"),
            arrive_time=raw.get("arrive_time") or raw.get("arrivalTime") or raw.get("arrTime"),
            aircraft=raw.get("aircraft") or raw.get("aircraftType"),
            ref_price=_as_float(raw.get("ref_price") or raw.get("price")),
            source="community_mcp",
            status="ok",
        )
    except Exception:  # noqa: BLE001
        logger.warning("A' flight MCP returned unexpected shape; degrading to B")
        return None


def _as_float(value) -> float | None:  # noqa: ANN001
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _invoke_mcp(origin: str, destination: str, date: str) -> dict | None:
    """Invoke the flight community MCP `searchFlightRoutes` over stdio.

    Uses the server's exact arguments (`departure_city`/`destination_city`/
    `departure_date`) and sets `MCP_TRANSPORT=stdio`. Detects the server's
    `status="error"` envelope (e.g. missing optional deps) and empty results,
    returning None so the caller degrades to B. Bounded by
    `FLIGHT_MCP_TIMEOUT_SECONDS`. Never raises.
    """
    from app.ai.tools import mcp_stdio

    if not origin or not destination:
        return None
    arguments = {
        "departure_city": origin,
        "destination_city": destination,
        "departure_date": date,
    }
    result = mcp_stdio.call_stdio_tool_sync(
        settings.FLIGHT_MCP_ENDPOINT,
        "searchFlightRoutes",
        arguments,
        extra_env={"MCP_TRANSPORT": "stdio"},
        timeout=settings.FLIGHT_MCP_TIMEOUT_SECONDS,
    )
    if not result or not isinstance(result, dict):
        return None
    if str(result.get("status", "")).lower() == "error":
        return None
    for key in ("flights", "routes", "data", "results"):
        rows = result.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
    if result.get("flight_no") or result.get("flightNo") or result.get("flight_number"):
        return result
    return None
