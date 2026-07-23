"""B-class entry-guide providers (《外部事实源与工具调用规范》二.3、三).

Deterministic official-channel guidance generated from local templates. These
never make network requests, are always `needs_official_confirmation`, and stay
available even when `TOOLS_ENABLED=false`. They contain only platform names,
official App/site names, suggested query conditions and generic notes — never
fabricated prices, availability or same-day policy.
"""

from __future__ import annotations

from app.ai.tools.schemas import BookingEvidence

# Provider identifiers for tool_call_logs (《数据库表结构与迁移规范》十二).
PROVIDER_RAIL = "rail_entry_guide_12306"
PROVIDER_FLIGHT_HOTEL = "flight_hotel_entry_guide_ctrip"
PROVIDER_LOCAL_LIFE = "local_life_entry_guide_meituan"

TOOL_RAIL = "rail_entry_guide"
TOOL_FLIGHT_HOTEL = "flight_hotel_entry_guide"
TOOL_LOCAL_LIFE = "local_life_entry_guide"

_STATUS = "needs_official_confirmation"


def rail_entry_guide(
    origin: str | None = None, destination: str | None = None, date: str | None = None
) -> BookingEvidence:
    """Official rail-ticket entry guidance (12306)."""
    leg = ""
    if origin and destination:
        leg = f"{origin}→{destination} "
    hint = f"{date or ''} {leg}动车/高铁车次".strip()
    return BookingEvidence(
        booking_type="火车票",
        official_channel="12306 官方 App/网站",
        query_hint=hint or "目标日期与起终点的火车票车次",
        notes="请在 12306 官方渠道查询车次、席别与时刻并尽早购票或候补；以官方实时为准。",
        status=_STATUS,
    )


def flight_hotel_entry_guide(
    origin: str | None = None,
    destination: str | None = None,
    date: str | None = None,
    *,
    is_hotel: bool = False,
) -> BookingEvidence:
    """Official flight / hotel entry guidance."""
    if is_hotel:
        return BookingEvidence(
            booking_type="酒店",
            official_channel="携程/各酒店官方 App",
            query_hint=f"{date or ''} {destination or ''} 酒店".strip() or "目的地酒店",
            notes="请在官方平台核对房型、房态与价格并尽早预订；以官方实时为准。",
            status=_STATUS,
        )
    leg = ""
    if origin and destination:
        leg = f"{origin}→{destination} "
    hint = f"{date or ''} {leg}航班".strip()
    return BookingEvidence(
        booking_type="机票",
        official_channel="携程/航司官方 App",
        query_hint=hint or "目标日期与起终点的航班",
        notes="请在航司或官方平台查询航班号、时刻与票价并尽早购票；以航司官方实时为准。",
        status=_STATUS,
    )


def local_life_entry_guide(city: str | None = None) -> BookingEvidence:
    """Official dining / ticket / local-life entry guidance."""
    return BookingEvidence(
        booking_type="餐饮/门票/本地生活",
        official_channel="美团/大众点评 App、景区官方小程序",
        query_hint=f"{city or ''} 餐饮、门票与本地生活".strip(),
        notes="请在官方平台或景区官方渠道查询营业时间、门票预约与当天政策；以官方实时为准。",
        status=_STATUS,
    )
