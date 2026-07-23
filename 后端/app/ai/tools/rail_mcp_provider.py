"""A′ rail community-MCP adapter (《外部事实源与工具调用规范》二.2、三·补充).

Wraps a 12306 community MCP server behind a synchronous `query_rail_sync(...)`
so endpoint/service code stays synchronous. Probe + timeout + exception →
return None, which triggers B-class degradation in `travel_fact_service`.
NEVER let A′ exceptions bubble to the business layer.

Default toolset targets **drfccv/mcp-server-12306** (`uvx mcp-server-12306`):
`search-stations` to resolve station codes, then `query-tickets` (args
`from_station`/`to_station`/`train_date`, station telecodes). The **node**
package (`npx -y 12306-mcp`, tools `get-station-code-of-citys` / `get-tickets`)
is kept only as a compatibility fallback, selected by endpoint string.

A′ is reference-level only ("以官方实时为准"); research/demo, low-frequency.
"""

from __future__ import annotations

import logging
import re

from app.ai.tools.schemas import RailFact
from app.core.config import settings

logger = logging.getLogger("travelplanet")


def is_enabled() -> bool:
    """A′ rail is attempted only when enabled with a configured endpoint."""
    return bool(
        settings.TOOLS_ENABLED
        and settings.RAIL_MCP_ENABLED
        and settings.RAIL_MCP_ENDPOINT
    )


def runtime_status() -> str:
    """Expose the MCP runtime state without starting or calling the endpoint."""
    if not is_enabled():
        return "disabled"
    from app.ai.tools import mcp_stdio

    return mcp_stdio.get_runtime_status(settings.RAIL_MCP_ENDPOINT)


def query_rail_sync(origin: str, destination: str, date: str) -> RailFact | None:
    """Query reference rail facts → normalized RailFact, or None (caller → B).

    Any unavailability/timeout/exception/empty/error result returns None so the
    caller silently degrades to the B-class `rail_entry_guide`.
    """
    facts = query_rail_options_sync(origin, destination, date, max_options=1)
    return facts[0] if facts else None


def query_rail_options_sync(
    origin: str, destination: str, date: str, *, max_options: int = 16
) -> list[RailFact]:
    """Query reference rail candidates, recommended options first.

    The MCP only supports origin/destination/date. We therefore fetch the day
    result once and select representative candidates from 07:00-22:00, one per
    hour when available, prioritizing faster higher-grade trains.
    """
    if not is_enabled():
        return []
    try:
        norms = _invoke_mcp_options(origin, destination, date)
    except Exception:  # noqa: BLE001
        logger.exception("A' rail MCP failed; degrading to B (no exception bubbled)")
        return []
    selected = _select_train_options(norms, max_options=max_options)
    facts: list[RailFact] = []
    for norm in selected:
        if not norm or not norm.get("train_no"):
            continue
        try:
            facts.append(
                RailFact(
                    origin=origin,
                    destination=destination,
                    date=date,
                    train_no=norm.get("train_no"),
                    depart_time=norm.get("depart_time"),
                    arrive_time=norm.get("arrive_time"),
                    duration=norm.get("duration"),
                    seat_class=norm.get("seat_class"),
                    ref_price=norm.get("ref_price"),
                    source="community_mcp",
                    status="ok",
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("A' rail MCP returned unexpected row; skipping")
    return facts


def _rail_fact_from_norm(origin: str, destination: str, date: str, norm: dict) -> RailFact | None:
    if not norm or not norm.get("train_no"):
        return None
    try:
        return RailFact(
            origin=origin,
            destination=destination,
            date=date,
            train_no=norm.get("train_no"),
            depart_time=norm.get("depart_time"),
            arrive_time=norm.get("arrive_time"),
            duration=norm.get("duration"),
            seat_class=norm.get("seat_class"),
            ref_price=norm.get("ref_price"),
            source="community_mcp",
            status="ok",
        )
    except Exception:  # noqa: BLE001
        logger.warning("A' rail MCP returned unexpected shape; degrading to B")
        return None


# ============================================================
# Helpers
# ============================================================


def _as_float(value) -> float | None:  # noqa: ANN001
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _cheapest_seat(prices) -> tuple[str | None, float | None]:  # noqa: ANN001
    """Pick the lowest-priced seat class from a 12306 `prices` list."""
    if not isinstance(prices, list):
        return None, None
    candidates = []
    for p in prices:
        if not isinstance(p, dict):
            continue
        price = _as_float(p.get("price"))
        if price is not None:
            candidates.append((price, p.get("seat_name") or p.get("seat_type_name")))
    if not candidates:
        return None, None
    price, name = min(candidates, key=lambda x: x[0])
    return name, price


def _normalize_row(row: dict) -> dict | None:
    """Normalize a server-specific ticket row into a common shape.

    Common keys: train_no (human code), depart_time, arrive_time, duration,
    seat_class, ref_price. Tolerates field-name differences across servers.
    """
    if not isinstance(row, dict):
        return None
    train_no = (
        row.get("start_train_code")
        or row.get("station_train_code")
        or row.get("train_code")
        or row.get("trainCode")
        or row.get("train_no")
    )
    depart_time = row.get("start_time") or row.get("from_time") or row.get("depart_time")
    arrive_time = row.get("arrive_time") or row.get("to_time")
    duration = row.get("lishi") or row.get("run_time") or row.get("duration")
    seat_class, ref_price = _cheapest_seat(row.get("prices") or row.get("ticket_info"))
    if ref_price is None:
        ref_price = _as_float(row.get("ref_price") or row.get("price"))
    if seat_class is None:
        seat_class = row.get("seat_class") or row.get("seat_name")
    return {
        "train_no": train_no,
        "depart_time": depart_time,
        "arrive_time": arrive_time,
        "duration": duration,
        "seat_class": seat_class,
        "ref_price": ref_price,
    }


def _rows(result) -> list[dict]:  # noqa: ANN001
    """Extract ticket row dicts from a tool result."""
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if isinstance(result, dict):
        for key in ("tickets", "trains", "data", "results"):
            rows = result.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        if result.get("start_train_code") or result.get("station_train_code") or result.get("train_no"):
            return [result]
    return []


def _first_row(result) -> dict | None:  # noqa: ANN001
    """Extract the first ticket row dict from a tool result."""
    rows = _rows(result)
    return rows[0] if rows else None


def _invoke_mcp(origin: str, destination: str, date: str) -> dict | None:
    """Dispatch to the drfccv (default) or node (fallback) toolset."""
    endpoint = settings.RAIL_MCP_ENDPOINT
    if not origin or not destination:
        return None
    # Node package fallback only when the endpoint clearly references it.
    if "12306-mcp" in endpoint and "mcp-server-12306" not in endpoint:
        return _invoke_node(endpoint, origin, destination, date)
    return _invoke_drfccv(endpoint, origin, destination, date)


def _invoke_mcp_options(origin: str, destination: str, date: str) -> list[dict]:
    """Dispatch and normalize all rows from the selected MCP toolset."""
    endpoint = settings.RAIL_MCP_ENDPOINT
    if not origin or not destination:
        return []
    if "12306-mcp" in endpoint and "mcp-server-12306" not in endpoint:
        return _invoke_node_options(endpoint, origin, destination, date)
    return _invoke_drfccv_options(endpoint, origin, destination, date)


# —— drfccv/mcp-server-12306 (default) ——

def _drfccv_resolve_code(endpoint: str, city: str) -> str | None:
    from app.ai.tools import mcp_stdio

    res = mcp_stdio.call_stdio_tool_sync(
        endpoint, "search-stations", {"query": city, "limit": 5},
        timeout=settings.TOOL_TIMEOUT_SECONDS,
    )
    if not isinstance(res, dict):
        return None
    stations = res.get("stations")
    if not isinstance(stations, list) or not stations:
        return None
    # Prefer an exact name match, else the first result.
    exact = next((s for s in stations if isinstance(s, dict) and s.get("name") == city), None)
    chosen = exact or stations[0]
    if isinstance(chosen, dict):
        return chosen.get("code") or chosen.get("station_code")
    return None


def _invoke_drfccv(endpoint: str, origin: str, destination: str, date: str) -> dict | None:
    from app.ai.tools import mcp_stdio

    from_code = _drfccv_resolve_code(endpoint, origin)
    to_code = _drfccv_resolve_code(endpoint, destination)
    if not from_code or not to_code:
        return None
    result = mcp_stdio.call_stdio_tool_sync(
        endpoint, "query-tickets",
        {"from_station": from_code, "to_station": to_code, "train_date": date},
        timeout=settings.TOOL_TIMEOUT_SECONDS,
    )
    if not isinstance(result, dict):
        return None
    if result.get("success") is False or str(result.get("status", "")).lower() == "error":
        return None
    row = _first_row(result)
    return _normalize_row(row) if row else None


def _invoke_drfccv_options(endpoint: str, origin: str, destination: str, date: str) -> list[dict]:
    from app.ai.tools import mcp_stdio

    from_code = _drfccv_resolve_code(endpoint, origin)
    to_code = _drfccv_resolve_code(endpoint, destination)
    if not from_code or not to_code:
        return []
    result = mcp_stdio.call_stdio_tool_sync(
        endpoint, "query-tickets",
        {"from_station": from_code, "to_station": to_code, "train_date": date},
        timeout=settings.TOOL_TIMEOUT_SECONDS,
    )
    if not isinstance(result, dict):
        return []
    if result.get("success") is False or str(result.get("status", "")).lower() == "error":
        return []
    return [norm for row in _rows(result) if (norm := _normalize_row(row))]


# —— node npx -y 12306-mcp (compatibility fallback) ——

def _node_resolve_code(endpoint: str, city: str) -> str | None:
    from app.ai.tools import mcp_stdio

    res = mcp_stdio.call_stdio_tool_sync(
        endpoint, "get-station-code-of-citys", {"citys": city},
        timeout=settings.TOOL_TIMEOUT_SECONDS,
    )
    if isinstance(res, dict):
        entry = res.get(city)
        if isinstance(entry, dict):
            return entry.get("station_code") or entry.get("stationCode")
    return None


def _invoke_node(endpoint: str, origin: str, destination: str, date: str) -> dict | None:
    from app.ai.tools import mcp_stdio

    from_code = _node_resolve_code(endpoint, origin)
    to_code = _node_resolve_code(endpoint, destination)
    if not from_code or not to_code:
        return None
    result = mcp_stdio.call_stdio_tool_sync(
        endpoint, "get-tickets",
        {"date": date, "fromStation": from_code, "toStation": to_code},
        timeout=settings.TOOL_TIMEOUT_SECONDS,
    )
    if not isinstance(result, dict):
        return None
    if str(result.get("status", "")).lower() == "error":
        return None
    # node 12306-mcp `get-tickets` returns human-readable text, surfaced as
    # `_raw_text`; parse the first train row from it.
    raw_text = result.get("_raw_text")
    if isinstance(raw_text, str) and raw_text:
        return _parse_node_tickets_text(raw_text)
    row = _first_row(result)
    return _normalize_row(row) if row else None


def _invoke_node_options(endpoint: str, origin: str, destination: str, date: str) -> list[dict]:
    from app.ai.tools import mcp_stdio

    from_code = _node_resolve_code(endpoint, origin)
    to_code = _node_resolve_code(endpoint, destination)
    if not from_code or not to_code:
        return []
    result = mcp_stdio.call_stdio_tool_sync(
        endpoint, "get-tickets",
        {"date": date, "fromStation": from_code, "toStation": to_code},
        timeout=settings.TOOL_TIMEOUT_SECONDS,
    )
    if not isinstance(result, dict):
        return []
    if str(result.get("status", "")).lower() == "error":
        return []
    raw_text = result.get("_raw_text")
    if isinstance(raw_text, str) and raw_text:
        return _parse_node_tickets_text_many(raw_text)
    return [norm for row in _rows(result) if (norm := _normalize_row(row))]


_TRAIN_LINE_RE = re.compile(r"^([GDCKTZYSL]?\d{1,4})\s")
_TIME_RE = re.compile(r"(\d{1,2}:\d{2})")
_DURATION_RE = re.compile(r"历时[:：]?\s*(\d{1,2}:\d{2})")
_SEAT_RE = re.compile(r"^-\s*(.+?)[:：]\s*.*?(\d+(?:\.\d+)?)\s*元\s*$")


def _parse_node_tickets_text(text: str) -> dict | None:
    """Parse node `12306-mcp` `get-tickets` plain-text output into a row dict.

    The output looks like (one train block, then its seat/price lines)::

        车次|出发站 -> 到达站|出发时间 -> 到达时间|历时
        G531 北京南(...) -> 上海虹桥(...) 06:08 -> 12:04 历时：05:56
        - 商务座: 剩余4张票 2156元
        - 一等座: 有票 967元
        - 二等座: 有票 576元

    Returns the FIRST train with its cheapest available seat as the reference
    price, or None when nothing parseable is found (caller degrades to B).
    """
    rows = _parse_node_tickets_text_many(text)
    return rows[0] if rows else None


def _parse_node_tickets_text_many(text: str) -> list[dict]:
    """Parse all train rows from node `12306-mcp` plain-text output."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows: list[dict] = []
    for idx, line in enumerate(lines):
        m = _TRAIN_LINE_RE.match(line)
        if not m or "->" not in line:
            continue
        train_no = m.group(1)
        times = _TIME_RE.findall(line)
        depart_time = times[0] if len(times) >= 1 else None
        arrive_time = times[1] if len(times) >= 2 else None
        dur = _DURATION_RE.search(line)
        duration = dur.group(1) if dur else None
        # Collect seat/price lines until the next train block.
        best: tuple[str, float] | None = None
        for seat_line in lines[idx + 1:]:
            if _TRAIN_LINE_RE.match(seat_line) and "->" in seat_line:
                break
            sm = _SEAT_RE.match(seat_line)
            if sm:
                name = sm.group(1).strip()
                price = _as_float(sm.group(2))
                if price is not None and (best is None or price < best[1]):
                    best = (name, price)
        seat_class = best[0] if best else None
        ref_price = best[1] if best else None
        rows.append(
            {
                "train_no": train_no,
                "depart_time": depart_time,
                "arrive_time": arrive_time,
                "duration": duration,
                "seat_class": seat_class,
                "ref_price": ref_price,
            }
        )
    return rows


def _select_train_options(norms: list[dict], *, max_options: int) -> list[dict]:
    """Select representative options from 07:00-22:00, recommended first."""
    cleaned = [row for row in norms if row.get("train_no") and _hour(row.get("depart_time")) is not None]
    by_hour: dict[int, list[dict]] = {}
    for row in cleaned:
        hour = _hour(row.get("depart_time"))
        if hour is not None and 7 <= hour <= 22:
            by_hour.setdefault(hour, []).append(row)

    selected: list[dict] = []
    for hour in range(7, 23):
        rows = by_hour.get(hour) or []
        if rows:
            selected.append(min(rows, key=_within_hour_score))

    if len(selected) < max_options:
        selected_ids = {id(row) for row in selected}
        remaining = [row for row in cleaned if id(row) not in selected_ids]
        selected.extend(sorted(remaining, key=_recommendation_score)[: max_options - len(selected)])

    return sorted(selected[:max_options], key=_recommendation_score)


def _within_hour_score(row: dict) -> tuple:
    return (_train_type_rank(row.get("train_no")), _duration_minutes(row.get("duration")) or 9999)


def _recommendation_score(row: dict) -> tuple:
    hour = _hour(row.get("depart_time")) or 0
    if 9 <= hour <= 18:
        time_rank = 0
    elif 8 <= hour <= 20:
        time_rank = 1
    elif 7 <= hour <= 22:
        time_rank = 2
    else:
        time_rank = 3
    return (
        time_rank,
        _train_type_rank(row.get("train_no")),
        _duration_minutes(row.get("duration")) or 9999,
        row.get("depart_time") or "",
    )


def _hour(time_text: str | None) -> int | None:
    if not time_text:
        return None
    m = re.match(r"^(\d{1,2}):\d{2}$", time_text)
    return int(m.group(1)) if m else None


def _duration_minutes(duration: str | None) -> int | None:
    if not duration:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", duration)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _train_type_rank(train_no: str | None) -> int:
    prefix = (train_no or "")[:1].upper()
    order = {"G": 0, "D": 1, "C": 2, "Z": 3, "T": 4, "K": 5}
    return order.get(prefix, 6)
