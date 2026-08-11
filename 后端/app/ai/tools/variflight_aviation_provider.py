"""VariFlight Aviation MCP adapter for pure flight transfer plans."""

from __future__ import annotations

from urllib.parse import urlsplit

from app.ai.tools.variflight_tripmatch_provider import (
    TripmatchCallResult,
    call_streamable_tool_sync,
)
from app.core.config import settings

PROVIDER = "variflight_aviation_mcp"
TOOL_SEARCH_FLIGHT_ITINERARIES = "searchFlightItineraries"
TOOL_SEARCH_FLIGHT_TRANSFER = "searchFlightsTransferinfo"


def is_configured() -> bool:
    return bool(
        settings.TOOLS_ENABLED
        and settings.VARIFLIGHT_AVIATION_MCP_ENABLED
        and settings.VARIFLIGHT_AVIATION_MCP_URL.strip()
        and settings.VARIFLIGHT_API_KEY.strip()
    )


def configuration_status() -> str:
    if not settings.TOOLS_ENABLED or not settings.VARIFLIGHT_AVIATION_MCP_ENABLED:
        return "disabled"
    if not settings.VARIFLIGHT_AVIATION_MCP_URL.strip():
        return "endpoint_missing"
    if not settings.VARIFLIGHT_API_KEY.strip():
        return "api_key_missing"
    return "configured"


def endpoint_identity() -> str:
    parts = urlsplit(settings.VARIFLIGHT_AVIATION_MCP_URL.strip())
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def search_flight_itineraries_sync(
    dep_city_code: str, dep_date: str, arr_city_code: str
) -> TripmatchCallResult:
    return call_streamable_tool_sync(
        TOOL_SEARCH_FLIGHT_ITINERARIES,
        {
            "depCityCode": dep_city_code,
            "depDate": dep_date,
            "arrCityCode": arr_city_code,
        },
        enabled=settings.TOOLS_ENABLED and settings.VARIFLIGHT_AVIATION_MCP_ENABLED,
        endpoint=settings.VARIFLIGHT_AVIATION_MCP_URL,
        api_key=settings.VARIFLIGHT_API_KEY,
        timeout_seconds=settings.VARIFLIGHT_AVIATION_TIMEOUT_SECONDS,
        provider_label="Aviation",
    )


def search_flight_transfer_sync(
    depcity: str, arrcity: str, depdate: str
) -> TripmatchCallResult:
    return call_streamable_tool_sync(
        TOOL_SEARCH_FLIGHT_TRANSFER,
        {"depcity": depcity, "arrcity": arrcity, "depdate": depdate},
        enabled=settings.TOOLS_ENABLED and settings.VARIFLIGHT_AVIATION_MCP_ENABLED,
        endpoint=settings.VARIFLIGHT_AVIATION_MCP_URL,
        api_key=settings.VARIFLIGHT_API_KEY,
        timeout_seconds=settings.VARIFLIGHT_AVIATION_TIMEOUT_SECONDS,
        provider_label="Aviation",
    )
