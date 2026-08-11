"""VariFlight Tripmatch Streamable HTTP MCP adapter.

Only the selected ``searchFlightandTrainTransferinfo`` capability is exposed
here. Tripmatch returns candidate information; rail segment schedules and
availability remain the responsibility of the existing 12306 MCP adapter. The
separate Aviation MCP adapter reuses the transport helper in this module.

The API key is appended to the request URL only at call time.  Public return
values and logs contain the endpoint host/path but never the key or signed URL.
"""

from __future__ import annotations

import asyncio
import ast
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.config import settings
from app.core.business_logging import log_event

logger = logging.getLogger("travelplanet")

PROVIDER = "variflight_tripmatch_mcp"
TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER = "searchFlightandTrainTransferinfo"


@dataclass(frozen=True)
class TripmatchCallResult:
    """Serializable, loss-aware view of one MCP tool call."""

    status: str
    provider_payload: Any = None
    raw_text: str | None = None
    content: list[Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    trace_id: str | None = None
    diagnostic_stage: str | None = None
    http_status: int | None = None
    exception_type: str | None = None
    latency_ms: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def is_configured() -> bool:
    return bool(
        settings.TOOLS_ENABLED
        and settings.VARIFLIGHT_TRIPMATCH_MCP_ENABLED
        and settings.VARIFLIGHT_TRIPMATCH_MCP_URL.strip()
        and settings.VARIFLIGHT_API_KEY.strip()
    )


def configuration_status() -> str:
    if not settings.TOOLS_ENABLED or not settings.VARIFLIGHT_TRIPMATCH_MCP_ENABLED:
        return "disabled"
    if not settings.VARIFLIGHT_TRIPMATCH_MCP_URL.strip():
        return "endpoint_missing"
    if not settings.VARIFLIGHT_API_KEY.strip():
        return "api_key_missing"
    return "configured"


def endpoint_identity() -> str:
    """Return a safe endpoint identity suitable for diagnostics."""
    parts = urlsplit(settings.VARIFLIGHT_TRIPMATCH_MCP_URL.strip())
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def search_flight_train_transfer_sync(
    depcity: str, arrcity: str, depdate: str
) -> TripmatchCallResult:
    return _call_tool_sync(
        TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER,
        {"depcity": depcity, "arrcity": arrcity, "depdate": depdate},
    )


def _call_tool_sync(tool_name: str, arguments: dict[str, str]) -> TripmatchCallResult:
    return call_streamable_tool_sync(
        tool_name,
        arguments,
        enabled=settings.TOOLS_ENABLED
        and settings.VARIFLIGHT_TRIPMATCH_MCP_ENABLED,
        endpoint=settings.VARIFLIGHT_TRIPMATCH_MCP_URL,
        api_key=settings.VARIFLIGHT_API_KEY,
        timeout_seconds=settings.VARIFLIGHT_TRIPMATCH_TIMEOUT_SECONDS,
        provider_label="Tripmatch",
    )


def call_streamable_tool_sync(
    tool_name: str,
    arguments: dict[str, str],
    *,
    enabled: bool,
    endpoint: str,
    api_key: str,
    timeout_seconds: int,
    provider_label: str,
) -> TripmatchCallResult:
    trace_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    config_status = _configuration_status(enabled, endpoint, api_key)
    safe_endpoint = _endpoint_identity(endpoint)
    safe_arguments = _safe_arguments(arguments)
    common = {
        "trace_id": trace_id,
        "provider": provider_label,
        "tool_name": tool_name,
        "endpoint": safe_endpoint,
        "arguments": safe_arguments,
        "timeout_seconds": timeout_seconds,
        "api_key_present": bool(api_key.strip()),
    }
    log_event(
        "variflight_mcp_call",
        status="start",
        configuration_status=config_status,
        **common,
    )
    if config_status != "configured":
        outcome = TripmatchCallResult(
            status="provider_not_connected",
            error_code=f"variflight_{config_status}",
            trace_id=trace_id,
            diagnostic_stage="configuration",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        _log_call_finish(outcome, common=common)
        return outcome
    diagnostics = {"stage": "dispatch"}
    try:
        outcome = asyncio.run(
            _call_tool(
                tool_name,
                arguments,
                endpoint=endpoint,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
                trace_id=trace_id,
                diagnostics=diagnostics,
            )
        )
        outcome = TripmatchCallResult(
            **{
                **outcome.__dict__,
                "trace_id": trace_id,
                "diagnostic_stage": diagnostics["stage"],
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        )
        _log_call_finish(outcome, common=common)
        return outcome
    except TimeoutError:
        logger.warning("%s MCP timed out (tool=%s)", provider_label, tool_name)
        outcome = TripmatchCallResult(
            status="timeout",
            error_code="variflight_timeout",
            error_message=f"{provider_label} MCP request timed out",
            trace_id=trace_id,
            diagnostic_stage=diagnostics["stage"],
            exception_type="TimeoutError",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        _log_call_finish(outcome, common=common)
        return outcome
    except BaseException as exc:  # MCP transports may surface an ExceptionGroup.
        status_code = _find_http_status(exc)
        if status_code in {401, 403}:
            code = "variflight_auth_failed"
        elif status_code == 429:
            code = "variflight_rate_limited"
        elif status_code and status_code >= 500:
            code = "variflight_upstream_error"
        else:
            code = "variflight_call_failed"
        logger.warning(
            "%s MCP call failed (tool=%s, code=%s, type=%s)",
            provider_label,
            tool_name,
            code,
            type(exc).__name__,
        )
        outcome = TripmatchCallResult(
            status="failed",
            error_code=code,
            error_message=_safe_error_message(exc, api_key=api_key),
            trace_id=trace_id,
            diagnostic_stage=diagnostics["stage"],
            http_status=status_code,
            exception_type=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        _log_call_finish(
            outcome,
            common=common,
            error_chain=_safe_exception_chain(exc, api_key=api_key),
        )
        return outcome


def _configuration_status(enabled: bool, endpoint: str, api_key: str) -> str:
    if not enabled:
        return "disabled"
    if not endpoint.strip():
        return "endpoint_missing"
    if not api_key.strip():
        return "api_key_missing"
    return "configured"


async def _call_tool(
    tool_name: str,
    arguments: dict[str, str],
    *,
    endpoint: str,
    api_key: str,
    timeout_seconds: int,
    trace_id: str,
    diagnostics: dict[str, str],
) -> TripmatchCallResult:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    timeout_seconds = max(1, timeout_seconds)
    request_url = _authenticated_url(endpoint, api_key)

    async def run() -> TripmatchCallResult:
        diagnostics["stage"] = "transport_connect"
        log_event("variflight_mcp_stage", status="start", trace_id=trace_id, stage=diagnostics["stage"])
        async with streamablehttp_client(
            request_url,
            timeout=timedelta(seconds=timeout_seconds),
            sse_read_timeout=timedelta(seconds=timeout_seconds),
        ) as (read_stream, write_stream, _):
            log_event("variflight_mcp_stage", status="success", trace_id=trace_id, stage="transport_connect")
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout_seconds),
            ) as session:
                diagnostics["stage"] = "session_initialize"
                log_event("variflight_mcp_stage", status="start", trace_id=trace_id, stage=diagnostics["stage"])
                await session.initialize()
                log_event("variflight_mcp_stage", status="success", trace_id=trace_id, stage=diagnostics["stage"])
                diagnostics["stage"] = "tool_call"
                log_event("variflight_mcp_stage", status="start", trace_id=trace_id, stage=diagnostics["stage"])
                result = await session.call_tool(tool_name, arguments=arguments)
                log_event("variflight_mcp_stage", status="success", trace_id=trace_id, stage=diagnostics["stage"])
                diagnostics["stage"] = "response_parse"
                return _extract_result(result)

    return await asyncio.wait_for(run(), timeout=timeout_seconds + 2)


def _authenticated_url(endpoint: str, api_key: str) -> str:
    parts = urlsplit(endpoint.strip())
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "api_key"]
    query.append(("api_key", api_key.strip()))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _extract_result(result: Any) -> TripmatchCallResult:
    """Preserve structured content, every text block and parsed JSON content."""
    structured = getattr(result, "structuredContent", None)
    content_blocks = getattr(result, "content", None) or []
    texts: list[str] = []
    parsed_content: list[Any] = []
    for block in content_blocks:
        text = getattr(block, "text", None)
        if not isinstance(text, str) or not text:
            continue
        texts.append(text)
        parsed = _parse_content_text(text)
        if parsed is not None:
            parsed_content.append(parsed)

    payload = structured
    if payload is None and len(parsed_content) == 1:
        payload = parsed_content[0]
    elif payload is None and parsed_content:
        payload = parsed_content

    is_error = bool(getattr(result, "isError", False))
    return TripmatchCallResult(
        status="failed" if is_error else "ok",
        provider_payload=payload,
        raw_text="\n".join(texts) if texts else None,
        content=parsed_content or None,
        error_code="variflight_provider_error" if is_error else None,
        error_message=("\n".join(texts)[:500] if is_error and texts else None),
    )


def _find_http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    nested = getattr(exc, "exceptions", None)
    if nested:
        for child in nested:
            found = _find_http_status(child)
            if found is not None:
                return found
    cause = getattr(exc, "__cause__", None)
    return _find_http_status(cause) if isinstance(cause, BaseException) else None


def _parse_content_text(text: str) -> Any | None:
    """Parse JSON or the MCP server's observed ``label: {python literal}`` form."""
    candidates = [text.strip()]
    first_brace = text.find("{")
    if first_brace > 0:
        candidates.append(text[first_brace:].strip())
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        if candidate.startswith(("{", "[")):
            try:
                parsed = ast.literal_eval(candidate)
            except (SyntaxError, ValueError):
                continue
            if isinstance(parsed, (dict, list)):
                return parsed
    return None


def _safe_error_message(exc: BaseException, *, api_key: str | None = None) -> str:
    """Keep diagnostics useful without ever returning the API key or signed URL."""
    message = str(exc)
    api_key = (
        settings.VARIFLIGHT_API_KEY if api_key is None else api_key
    ).strip()
    if api_key:
        message = message.replace(api_key, "***")
        encoded = urlencode({"api_key": api_key}).removeprefix("api_key=")
        message = message.replace(encoded, "***")
    message = re.sub(
        r"([?&]api_key=)[^&\s'\"]+",
        r"\1***",
        message,
        flags=re.IGNORECASE,
    )
    return message[:500]


def _endpoint_identity(endpoint: str) -> str:
    parts = urlsplit(endpoint.strip())
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _safe_arguments(arguments: dict[str, str]) -> dict[str, str]:
    """Flight search arguments are diagnostic-safe; still drop secret-like keys."""
    return {
        str(key): str(value)[:200]
        for key, value in arguments.items()
        if str(key).lower() not in {"api_key", "apikey", "key", "token", "authorization"}
    }


def _safe_exception_chain(
    exc: BaseException, *, api_key: str, limit: int = 6
) -> list[dict[str, str]]:
    chain: list[dict[str, str]] = []
    queue: list[BaseException] = [exc]
    seen: set[int] = set()
    while queue and len(chain) < limit:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(
            {
                "type": type(current).__name__,
                "message": _safe_error_message(current, api_key=api_key),
            }
        )
        nested = getattr(current, "exceptions", None)
        if nested:
            queue.extend(child for child in nested if isinstance(child, BaseException))
        cause = current.__cause__ or current.__context__
        if isinstance(cause, BaseException):
            queue.append(cause)
    return chain


def _log_call_finish(
    outcome: TripmatchCallResult,
    *,
    common: dict[str, Any],
    error_chain: list[dict[str, str]] | None = None,
) -> None:
    payload = outcome.provider_payload
    finish_status = (
        "success" if outcome.ok else "timeout" if outcome.status == "timeout" else "failed"
    )
    log_event(
        "variflight_mcp_call",
        status=finish_status,
        provider_status=outcome.status,
        diagnostic_stage=outcome.diagnostic_stage,
        latency_ms=outcome.latency_ms,
        http_status=outcome.http_status,
        exception_type=outcome.exception_type,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        error_chain=error_chain or [],
        payload_type=type(payload).__name__ if payload is not None else None,
        payload_keys=list(payload)[:30] if isinstance(payload, dict) else [],
        raw_text_chars=len(outcome.raw_text or ""),
        parsed_content_blocks=len(outcome.content or []),
        **common,
    )
