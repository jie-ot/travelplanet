"""Function-calling tool specifications for planning (《外部事实源与工具调用规范》五.4).

Defines the OpenAI-compatible `tools` schema exposed to the model during
`/api/ai/planning`, plus a per-external-tool Pydantic argument schema used by
`travel_fact_service.execute_tool` before any provider runs. External calls map
strictly to the provider whitelist; the internal `update_planning_fact_state`
call is handled locally by the orchestrator and never reaches a provider.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, field_validator

TOOL_AMAP_WEATHER = "amap_weather"
TOOL_AMAP_POI_SEARCH = "amap_poi_search"
TOOL_AMAP_POI_AROUND = "amap_poi_around"
TOOL_AMAP_ROUTE = "amap_route"
TOOL_QUERY_RAIL = "query_rail_tickets"
TOOL_QUERY_FLIGHTS = "query_flights"
TOOL_UPDATE_PLANNING_FACT_STATE = "update_planning_fact_state"


class WeatherArgs(BaseModel):
    city: str
    date: str  # YYYY-MM-DD

    @field_validator("city", "date")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()


class PoiArgs(BaseModel):
    keyword: str
    city: str | None = None

    @field_validator("keyword")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()


class PoiAroundArgs(BaseModel):
    location: str
    keywords: str
    radius: int = 1000
    city: str | None = None

    @field_validator("location")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()

    @field_validator("keywords")
    @classmethod
    def _normalize_keywords(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        parts = re.split(r"[|｜,，、;；/\s]+", v.strip())
        normalized = "|".join(dict.fromkeys(part for part in parts if part))
        if not normalized:
            raise ValueError("must contain at least one keyword")
        return normalized

    @field_validator("radius")
    @classmethod
    def _reasonable_radius(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return min(v, 50000)


class RouteArgs(BaseModel):
    origin: str
    destination: str
    mode: Literal["driving", "transit", "walking", "bicycling"]
    city: str | None = None

    @field_validator("origin", "destination")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()


class RailArgs(BaseModel):
    origin: str
    destination: str
    date: str  # YYYY-MM-DD

    @field_validator("origin", "destination", "date")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()


class FlightArgs(BaseModel):
    origin: str
    destination: str
    date: str  # YYYY-MM-DD

    @field_validator("origin", "destination", "date")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()


# tool_name → argument schema, used for validation in travel_fact_service.
ARG_SCHEMAS: dict[str, type[BaseModel]] = {
    TOOL_AMAP_WEATHER: WeatherArgs,
    TOOL_AMAP_POI_SEARCH: PoiArgs,
    TOOL_AMAP_POI_AROUND: PoiAroundArgs,
    TOOL_AMAP_ROUTE: RouteArgs,
    TOOL_QUERY_RAIL: RailArgs,
    TOOL_QUERY_FLIGHTS: FlightArgs,
}


# OpenAI-compatible tools array passed to the model (descriptions in Chinese so
# Doubao-Seed picks the right tool for travel intents).
PLANNING_EXTERNAL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": TOOL_AMAP_WEATHER,
            "description": "查询某城市在指定日期的天气（高德官方真实事实）。用于判断是否适合户外活动、是否需要带雨具/防晒等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名，如 “大理”、“北京”",
                    },
                    "date": {"type": "string", "description": "日期，格式 YYYY-MM-DD"},
                },
                "required": ["city", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_AMAP_POI_SEARCH,
            "description": "在全城或指定城市内按名称/类别搜索景点、酒店、餐饮、地标等 POI 候选及坐标；适合中心点尚未确定的选址，不用于已知酒店/景点的周边检索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词，如 “洱海”、“古城客栈”",
                    },
                    "city": {"type": "string", "description": "限定城市（可选）"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_AMAP_POI_AROUND,
            "description": "中心点确定后，按坐标或地名查询半径内的餐饮、便利店、景点等 POI；适合酒店周边吃饭、景区周边配套，不用于全城选址。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "中心点，优先传 POI 返回的经纬度 lng,lat；也可传酒店/地标名，后端会先解析坐标",
                    },
                    "keywords": {
                        "type": "string",
                        "description": "周边搜索关键词；多个关键词用 | 分隔，如 “餐饮|便利店|小吃”，不要用空格串联",
                    },
                    "radius": {
                        "type": "integer",
                        "description": "搜索半径，单位米，默认 1000",
                    },
                    "city": {"type": "string", "description": "限定城市（可选）"},
                },
                "required": ["location", "keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_AMAP_ROUTE,
            "description": "查询两地之间的路线距离与预计时长（高德官方真实事实）。支持 driving/walking/bicycling/transit，用于安排城市间或市内通勤节奏。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "string",
                        "description": "起点，优先传 POI 返回的 lng,lat；否则用站名/景点短名，避免完整商业 POI 名",
                    },
                    "destination": {
                        "type": "string",
                        "description": "终点，优先传 POI 返回的 lng,lat；否则用站名/景点短名，避免完整商业 POI 名",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["driving", "transit", "walking", "bicycling"],
                        "description": "实际计划采用的出行方式，必须明确选择：打车/自驾 driving，公交地铁 transit，步行 walking，骑行 bicycling",
                    },
                    "city": {
                        "type": "string",
                        "description": "公交/地铁 transit 模式的城市名或 adcode（可选但建议提供）",
                    },
                },
                "required": ["origin", "destination", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_QUERY_RAIL,
            "description": "查询两城之间某日期的火车票参考信息（车次/时刻/参考票价，来自社区 12306 MCP，参考级、非权威）。结果须以 12306 官方实时为准。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "出发城市"},
                    "destination": {"type": "string", "description": "到达城市"},
                    "date": {
                        "type": "string",
                        "description": "乘车日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["origin", "destination", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_QUERY_FLIGHTS,
            "description": "查询两城之间某日期的航班参考信息（航班号/时刻/参考票价，来自社区航班 MCP，参考级、非权威）。结果须以航司官方实时为准。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "出发城市"},
                    "destination": {"type": "string", "description": "到达城市"},
                    "date": {
                        "type": "string",
                        "description": "乘机日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["origin", "destination", "date"],
            },
        },
    },
]


# Internal orchestration tool: no provider call and no external-call budget.
# The model selects facts by backend-issued IDs; the orchestrator resolves each
# ID back to the complete original tool fact before compacting message history.
PLANNING_FACT_STATE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": TOOL_UPDATE_PLANNING_FACT_STATE,
        "description": (
            "读取新的外部工具结果、准备继续下一轮查询时更新 PlanningFactState。仅选择后续规划可能使用的"
            "fact_id，并说明用途；后端会完整保留所选事实的所有原始字段、裁剪未选事实。"
            "同时列出仍缺少的事实，以便继续主动查询。此工具不访问外部服务。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selected_facts": {
                    "type": "array",
                    "description": "本次规划仍可能使用的事实；必须包含此前仍需保留的事实",
                    "items": {
                        "type": "object",
                        "properties": {
                            "fact_id": {
                                "type": "string",
                                "description": "外部工具结果中的 fact_id",
                            },
                            "purpose": {
                                "type": "string",
                                "description": "该事实将用于酒店、景点、餐饮、交通、天气等哪项决策",
                            },
                        },
                        "required": ["fact_id", "purpose"],
                    },
                },
                "missing_facts": {
                    "type": "array",
                    "description": "扣除同一响应已安排的外部查询后，生成可执行行程仍缺少的具体事实；查询失败时下一轮补回",
                    "items": {"type": "string"},
                },
            },
            "required": ["selected_facts", "missing_facts"],
        },
    },
}

PLANNING_TOOLS: list[dict] = [*PLANNING_EXTERNAL_TOOLS, PLANNING_FACT_STATE_TOOL]
