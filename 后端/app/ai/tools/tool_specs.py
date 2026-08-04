"""Function-calling tool specifications for planning (《外部事实源与工具调用规范》五.4).

Defines the OpenAI-compatible `tools` schema exposed to the model during
`/api/ai/planning`, plus a per-external-tool Pydantic argument schema used by
`travel_fact_service.execute_tool` before any provider runs. External calls map
strictly to the provider whitelist; three internal workflow calls are handled
locally by the orchestrator and never reach a provider.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TOOL_AMAP_WEATHER_RANGE = "amap_weather_range"
TOOL_AMAP_POI_SEARCH = "amap_poi_search"
TOOL_AMAP_POI_AROUND = "amap_poi_around"
TOOL_AMAP_POI_DETAIL = "amap_poi_detail"
TOOL_AMAP_ROUTE = "amap_route"
TOOL_QUERY_RAIL = "query_rail_tickets"
TOOL_SEARCH_FLIGHT_ITINERARIES = "searchFlightItineraries"
TOOL_SEARCH_FLIGHT_TRANSFER = "searchFlightsTransferinfo"
TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER = "searchFlightandTrainTransferinfo"
TOOL_DECLARE_TRIP_SCOPE = "declare_trip_scope"
TOOL_UPDATE_PLANNING_FACT_STATE = "update_planning_fact_state"
TOOL_FINISH_RESEARCH = "finish_research"


class AliasModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class WeatherRangeArgs(AliasModel):
    city: str
    adcode: str | None = None
    start_date: str = Field(alias="startDate")
    end_date: str = Field(alias="endDate")

    @field_validator("city", "start_date", "end_date")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()


class PoiArgs(AliasModel):
    keyword: str | None = None
    types: str | None = None
    city: str | None = None
    city_limit: bool = Field(default=True, alias="cityLimit")
    limit: int = 12

    @field_validator("keyword", "types", "city")
    @classmethod
    def _clean_optional(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None

    @field_validator("keyword")
    @classmethod
    def _single_keyword(cls, v: str | None) -> str | None:
        if v and any(separator in v for separator in ("|", "｜")):
            raise ValueError("POI 2.0 accepts one keyword; use types or separate calls")
        return v

    @field_validator("limit")
    @classmethod
    def _limit(cls, v: int) -> int:
        return max(1, min(v, 25))

    @model_validator(mode="after")
    def _has_query(self) -> "PoiArgs":
        if not (self.keyword or self.types):
            raise ValueError("keyword or types is required")
        return self


class PoiAroundArgs(AliasModel):
    location: str
    keyword: str | None = None
    types: str | None = None
    radius: int = 1000
    city: str | None = None
    city_limit: bool = Field(default=True, alias="cityLimit")
    limit: int = 12
    sort_rule: Literal["distance", "weight"] = Field(
        default="distance", alias="sortRule"
    )

    @field_validator("location")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()

    @field_validator("keyword", "types", "city")
    @classmethod
    def _clean_optional(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None

    @field_validator("keyword")
    @classmethod
    def _single_keyword(cls, v: str | None) -> str | None:
        if v and any(separator in v for separator in ("|", "｜")):
            raise ValueError("POI 2.0 accepts one keyword; use types or separate calls")
        return v

    @field_validator("radius")
    @classmethod
    def _reasonable_radius(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return min(v, 50000)

    @field_validator("limit")
    @classmethod
    def _limit(cls, v: int) -> int:
        return max(1, min(v, 25))

    @model_validator(mode="after")
    def _has_query(self) -> "PoiAroundArgs":
        if not (self.keyword or self.types):
            raise ValueError("keyword or types is required")
        return self


class PoiDetailArgs(AliasModel):
    poi_ids: list[str] = Field(alias="poiIds", min_length=1, max_length=10)

    @field_validator("poi_ids")
    @classmethod
    def _clean_ids(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not cleaned:
            raise ValueError("poiIds must contain at least one non-empty id")
        return cleaned[:10]


class RouteArgs(AliasModel):
    origin: str | None = None
    destination: str | None = None
    origin_location: str | None = Field(default=None, alias="originLocation")
    destination_location: str | None = Field(default=None, alias="destinationLocation")
    origin_poi_id: str | None = Field(default=None, alias="originPoiId")
    destination_poi_id: str | None = Field(default=None, alias="destinationPoiId")
    origin_adcode: str | None = Field(default=None, alias="originAdcode")
    destination_adcode: str | None = Field(default=None, alias="destinationAdcode")
    origin_citycode: str | None = Field(default=None, alias="originCitycode")
    destination_citycode: str | None = Field(default=None, alias="destinationCitycode")
    waypoint_locations: list[str] = Field(
        default_factory=list, alias="waypointLocations", max_length=16
    )
    destination_type: str | None = Field(default=None, alias="destinationType")
    vehicle_plate: str | None = Field(default=None, alias="vehiclePlate")
    car_type: Literal[0, 1, 2] = Field(default=0, alias="carType")
    avoid_ferry: bool = Field(default=False, alias="avoidFerry")
    night_service: bool = Field(default=False, alias="nightService")
    mode: Literal["driving", "transit", "walking", "bicycling"]
    city: str | None = None
    strategy: int | None = None
    alternatives: int = 3

    @field_validator(
        "origin",
        "destination",
        "origin_location",
        "destination_location",
        "origin_poi_id",
        "destination_poi_id",
        "origin_adcode",
        "destination_adcode",
        "origin_citycode",
        "destination_citycode",
        "destination_type",
        "vehicle_plate",
    )
    @classmethod
    def _clean_optional(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None

    @field_validator("waypoint_locations")
    @classmethod
    def _waypoints(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value and value.strip()][:16]

    @field_validator("alternatives")
    @classmethod
    def _alternatives(cls, v: int) -> int:
        return max(1, min(v, 10))

    @model_validator(mode="after")
    def _has_both_endpoints(self) -> "RouteArgs":
        if not (self.origin or self.origin_location):
            raise ValueError("origin or originLocation is required")
        if not (self.destination or self.destination_location):
            raise ValueError("destination or destinationLocation is required")
        return self


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


class FlightItinerariesArgs(AliasModel):
    dep_city_code: str = Field(alias="depCityCode")
    dep_date: str = Field(alias="depDate")
    arr_city_code: str = Field(alias="arrCityCode")

    @field_validator("dep_city_code", "arr_city_code")
    @classmethod
    def _iata_city_code(cls, v: str) -> str:
        code = v.strip().upper()
        if len(code) != 3 or not code.isascii() or not code.isalpha():
            raise ValueError("must be a three-letter IATA city code")
        return code

    @field_validator("dep_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        value = v.strip()
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _different_cities(self) -> "FlightItinerariesArgs":
        if self.dep_city_code == self.arr_city_code:
            raise ValueError("depCityCode and arrCityCode must be different")
        return self


class FlightTrainTransferArgs(BaseModel):
    depcity: str
    arrcity: str
    depdate: str

    @field_validator("depcity", "arrcity")
    @classmethod
    def _iata_city_code(cls, v: str) -> str:
        code = v.strip().upper()
        if len(code) != 3 or not code.isascii() or not code.isalpha():
            raise ValueError("must be a three-letter IATA city code")
        return code

    @field_validator("depdate")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        value = v.strip()
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _different_cities(self) -> "FlightTrainTransferArgs":
        if self.depcity == self.arrcity:
            raise ValueError("depcity and arrcity must be different")
        return self


class DeclareTripScopeArgs(AliasModel):
    origin: str | None = None
    destinations: list[str]
    start_date: str | None = Field(default=None, alias="startDate")
    end_date: str | None = Field(default=None, alias="endDate")
    needs_transport: bool = Field(alias="needsTransport")
    needs_hotel: bool = Field(alias="needsHotel")
    interests: list[str] = []
    uncertainties: list[str] = []


class PlanningFactStateArgs(AliasModel):
    selected_fact_ids: list[str] = Field(alias="selectedFactIds")
    remaining_queries: list[str] = Field(alias="remainingQueries")


class FinishResearchArgs(BaseModel):
    completed: list[str]
    unresolved: list[str]
    outline: list[str]


# tool_name → argument schema, used for validation in travel_fact_service.
ARG_SCHEMAS: dict[str, type[BaseModel]] = {
    TOOL_AMAP_WEATHER_RANGE: WeatherRangeArgs,
    TOOL_AMAP_POI_SEARCH: PoiArgs,
    TOOL_AMAP_POI_AROUND: PoiAroundArgs,
    TOOL_AMAP_POI_DETAIL: PoiDetailArgs,
    TOOL_AMAP_ROUTE: RouteArgs,
    TOOL_QUERY_RAIL: RailArgs,
    TOOL_SEARCH_FLIGHT_ITINERARIES: FlightItinerariesArgs,
    TOOL_SEARCH_FLIGHT_TRANSFER: FlightTrainTransferArgs,
    TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER: FlightTrainTransferArgs,
}


# Canonical OpenAI-compatible external tool definitions. Model-specific
# filtering below keeps Tripmatch flight tools away from Doubao.
PLANNING_EXTERNAL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": TOOL_AMAP_WEATHER_RANGE,
            "description": "一次查询一个城市的高德天气预报。官方接口通常仅覆盖今天及未来两天；更远日期会明确返回 unknown，禁止用首日预报冒充。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名，如 “大理”、“北京”",
                    },
                    "adcode": {
                        "type": "string",
                        "description": "POI 结果中的城市 adcode（可选；提供后可省去一次行政区解析）",
                    },
                    "startDate": {"type": "string", "description": "开始日期，格式 YYYY-MM-DD"},
                    "endDate": {"type": "string", "description": "结束日期，格式 YYYY-MM-DD"},
                },
                "required": ["city", "startDate", "endDate"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_AMAP_POI_SEARCH,
            "description": "高德 POI 2.0 全城检索，一次最多返回 25 个含 POI ID、坐标、行政区和营业信息的候选。keyword 只能是一个意图；多类别应传 types 或并行拆成多个调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "单个搜索关键词，如 “洱海”或“古城客栈”；不得用 | 拼接多个意图",
                    },
                    "types": {
                        "type": "string",
                        "description": "高德 POI 分类编码；多个分类编码可用 | 分隔（与 keyword 至少提供一个）",
                    },
                    "city": {"type": "string", "description": "限定城市（可选）"},
                    "cityLimit": {
                        "type": "boolean",
                        "description": "有 city 时是否严格限制在该城市，默认 true",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 25,
                        "description": "本次返回数，默认 12，最多 25",
                    },
                },
                "anyOf": [{"required": ["keyword"]}, {"required": ["types"]}],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_AMAP_POI_AROUND,
            "description": "高德 POI 2.0 周边检索，一次最多返回 25 个带距离的候选。适合酒店/景区周边配套；keyword 仅一个意图，多类别传 types 或并行拆分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "中心点，优先传 POI 返回的经纬度 lng,lat；也可传酒店/地标名，后端会先解析坐标",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "单个周边搜索意图，如 “云南菜”或“便利店”；不得用 | 拼接",
                    },
                    "types": {
                        "type": "string",
                        "description": "高德 POI 分类编码；多个分类编码可用 | 分隔（与 keyword 至少提供一个）",
                    },
                    "radius": {
                        "type": "integer",
                        "description": "搜索半径，单位米，默认 1000",
                    },
                    "city": {"type": "string", "description": "限定城市（可选）"},
                    "cityLimit": {"type": "boolean", "description": "严格限城，默认 true"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25, "description": "返回数，默认 12，最多 25"},
                    "sortRule": {"type": "string", "enum": ["distance", "weight"], "description": "按距离或综合权重排序"},
                },
                "required": ["location"],
                "anyOf": [{"required": ["keyword"]}, {"required": ["types"]}],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_AMAP_POI_DETAIL,
            "description": "按 POI 搜索返回的 ID 批量补齐已入选主景点/酒店的详细信息、入口坐标、营业时间、评分等；一次最多 10 个，不要对所有候选机械调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "poiIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 10,
                        "description": "已选 POI 的高德 ID 列表",
                    }
                },
                "required": ["poiIds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_AMAP_ROUTE,
            "description": "高德路径规划 2.0：驾车/步行/骑行/公交的距离、时长、费用、换乘与分步路线，并可保留多条备选。已有 POI ID、坐标、citycode 时一并传入以提高精度并减少内部查询。",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "string",
                        "description": "起点名称；已有坐标时仍建议提供，便于最终展示",
                    },
                    "destination": {
                        "type": "string",
                        "description": "终点名称；已有坐标时仍建议提供，便于最终展示",
                    },
                    "originLocation": {
                        "type": "string",
                        "description": "起点 POI 坐标 lng,lat；提供后不再地理编码",
                    },
                    "destinationLocation": {
                        "type": "string",
                        "description": "终点 POI 坐标 lng,lat；提供后不再地理编码",
                    },
                    "originPoiId": {"type": "string", "description": "起点高德 POI ID（可选）"},
                    "destinationPoiId": {"type": "string", "description": "终点高德 POI ID（可选）"},
                    "originAdcode": {"type": "string", "description": "起点 adcode（公交建议）"},
                    "destinationAdcode": {"type": "string", "description": "终点 adcode（公交建议）"},
                    "originCitycode": {"type": "string", "description": "起点 citycode；公交模式缺失时后端会逆地理解析"},
                    "destinationCitycode": {"type": "string", "description": "终点 citycode；公交模式缺失时后端会逆地理解析"},
                    "waypointLocations": {"type": "array", "items": {"type": "string"}, "maxItems": 16, "description": "驾车模式有序途经点坐标，最多 16 个；只用于明确的自驾游玩顺序"},
                    "destinationType": {"type": "string", "description": "驾车终点 POI 分类编码，可提升终点匹配"},
                    "vehiclePlate": {"type": "string", "description": "自驾车牌；仅用户明确提供时传，用于限行判断"},
                    "carType": {"type": "integer", "enum": [0, 1, 2], "description": "0 燃油、1 纯电、2 插混；默认 0"},
                    "avoidFerry": {"type": "boolean", "description": "驾车是否避开轮渡，默认 false"},
                    "nightService": {"type": "boolean", "description": "公交是否考虑夜班车，默认 false"},
                    "mode": {
                        "type": "string",
                        "enum": ["driving", "transit", "walking", "bicycling"],
                        "description": "实际计划采用的出行方式，必须明确选择：打车/自驾 driving，公交地铁 transit，步行 walking，骑行 bicycling",
                    },
                    "city": {
                        "type": "string",
                        "description": "兼容字段：地点解析时使用的城市名",
                    },
                    "strategy": {"type": "integer", "description": "高德对应方式的策略值；不确定时省略使用后端默认"},
                    "alternatives": {"type": "integer", "minimum": 1, "maximum": 10, "description": "希望保留的备选数；步行/骑行最多 3，公交最多 10，驾车以接口实返为准"},
                },
                "required": ["mode"],
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
            "name": TOOL_SEARCH_FLIGHT_ITINERARIES,
            "description": "飞友 Aviation MCP 指定日期城市对航班方案查询。输入出发城市、到达城市 IATA 三字码和出发日期，保留上游返回的全部候选及最低价、最短耗时和推荐方案，字段可包含航班号、完整起降日期时间、耗时、是否中转、舱等与价格。适合回答某日从某城飞往某城的可选班次；最终仍须去重共享航班、区分实际承运航司，并以航司或正规售票平台实时信息为准。",
            "parameters": {
                "type": "object",
                "properties": {
                    "depCityCode": {
                        "type": "string",
                        "pattern": "^[A-Za-z]{3}$",
                        "description": "出发城市 IATA 三字码，如 WUH",
                    },
                    "depDate": {
                        "type": "string",
                        "format": "date",
                        "description": "出发日期，格式 YYYY-MM-DD",
                    },
                    "arrCityCode": {
                        "type": "string",
                        "pattern": "^[A-Za-z]{3}$",
                        "description": "到达城市 IATA 三字码，如 SZX",
                    },
                },
                "required": ["depCityCode", "depDate", "arrCityCode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_SEARCH_FLIGHT_TRANSFER,
            "description": "飞友 Aviation MCP 指定日期纯航班一次中转方案查询。输入出发机场、到达机场 IATA 三字码和计划起飞日期，返回查询时点起至多未来 48 小时内的一程航班中转候选，并保留出发地/机场、到达地/机场、每段航班号、平均延误、航站楼、时区等完整上游字段。只用于需要中转或直飞不可用时，不要替代直飞查询。",
            "parameters": {
                "type": "object",
                "properties": {
                    "depcity": {
                        "type": "string",
                        "pattern": "^[A-Za-z]{3}$",
                        "description": "出发机场 IATA 三字码，如 SZX",
                    },
                    "arrcity": {
                        "type": "string",
                        "pattern": "^[A-Za-z]{3}$",
                        "description": "到达机场 IATA 三字码，如 URC",
                    },
                    "depdate": {
                        "type": "string",
                        "format": "date",
                        "description": "航班计划起飞日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["depcity", "arrcity", "depdate"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER,
            "description": "飞友 Tripmatch 指定日期航班/铁路一次中转方案查询。输入出发、到达城市 IATA 三字码和日期，保留上游返回的完整中转结构，用于比较直飞、经邻近机场或空铁联运候选。此工具只发现候选拓扑；其中每一段铁路班次、时刻、票价和余票必须再调用 query_rail_tickets，由原 12306 MCP 核验后才能写入行程。",
            "parameters": {
                "type": "object",
                "properties": {
                    "depcity": {
                        "type": "string",
                        "pattern": "^[A-Za-z]{3}$",
                        "description": "出发城市 IATA 三字码，如 NKG",
                    },
                    "arrcity": {
                        "type": "string",
                        "pattern": "^[A-Za-z]{3}$",
                        "description": "到达城市 IATA 三字码，如 SZX",
                    },
                    "depdate": {
                        "type": "string",
                        "format": "date",
                        "description": "出发日期，格式 YYYY-MM-DD",
                    },
                },
                "required": ["depcity", "arrcity", "depdate"],
            },
        },
    },
]

DEEPSEEK_ONLY_EXTERNAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        TOOL_SEARCH_FLIGHT_ITINERARIES,
        TOOL_SEARCH_FLIGHT_TRANSFER,
        TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER,
    }
)
DEEPSEEK_ONLY_EXTERNAL_TOOLS: list[dict] = [
    tool
    for tool in PLANNING_EXTERNAL_TOOLS
    if tool["function"]["name"] in DEEPSEEK_ONLY_EXTERNAL_TOOL_NAMES
]
PLANNING_EXTERNAL_TOOLS = [
    tool
    for tool in PLANNING_EXTERNAL_TOOLS
    if tool["function"]["name"] not in DEEPSEEK_ONLY_EXTERNAL_TOOL_NAMES
]


def planning_external_tools_for_model(planning_model: str) -> list[dict]:
    tools = [*PLANNING_EXTERNAL_TOOLS]
    if planning_model.startswith("deepseek-"):
        tools.extend(DEEPSEEK_ONLY_EXTERNAL_TOOLS)
    return tools


def external_tool_names_for_model(planning_model: str) -> frozenset[str]:
    return frozenset(
        tool["function"]["name"]
        for tool in planning_external_tools_for_model(planning_model)
    )

# Requirement intake may answer lightweight factual questions before the user
# confirms generation, but it must not access the internal research protocol.
PLANNING_INTAKE_TOOLS: list[dict] = [*PLANNING_EXTERNAL_TOOLS]


def planning_intake_tools_for_model(planning_model: str) -> list[dict]:
    return planning_external_tools_for_model(planning_model)


PLANNING_SCOPE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": TOOL_DECLARE_TRIP_SCOPE,
        "description": "声明你从用户自然语言和已有 context 中理解出的旅行范围。每次规划研究开始必须先调用；后端只保存，不自行提取或改写。",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {
                    "anyOf": [{"type": "string"}, {"type": "null"}]
                },
                "destinations": {"type": "array", "items": {"type": "string"}},
                "startDate": {
                    "anyOf": [{"type": "string"}, {"type": "null"}]
                },
                "endDate": {
                    "anyOf": [{"type": "string"}, {"type": "null"}]
                },
                "needsTransport": {"type": "boolean"},
                "needsHotel": {"type": "boolean"},
                "interests": {"type": "array", "items": {"type": "string"}},
                "uncertainties": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "origin",
                "destinations",
                "startDate",
                "endDate",
                "needsTransport",
                "needsHotel",
                "interests",
                "uncertainties",
            ],
        },
    },
}


PLANNING_FACT_STATE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": TOOL_UPDATE_PLANNING_FACT_STATE,
        "description": (
            "从已返回结果中选择后续生成行程需要保留的 fact_id，并列出还需要执行的具体查询。"
            "后端会按 ID 完整保留事实；此工具不访问外部服务，也不占外部调用预算。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selectedFactIds": {
                    "type": "array",
                    "description": "后续仍需使用的事实 ID；必须重新包含此前仍需保留的 ID",
                    "items": {"type": "string"},
                },
                "remainingQueries": {
                    "type": "array",
                    "description": "仍待完成的具体查询，例如酒店到主要景点路线",
                    "items": {"type": "string"},
                },
            },
            "required": ["selectedFactIds", "remainingQueries"],
        },
    },
}


PLANNING_FINISH_TOOL: dict = {
    "type": "function",
    "function": {
        "name": TOOL_FINISH_RESEARCH,
        "description": "仅在关键事实查询已经完成或明确列入 unresolved 后调用。调用后后端关闭工具并让同一个模型生成最终行程。",
        "parameters": {
            "type": "object",
            "properties": {
                "completed": {"type": "array", "items": {"type": "string"}},
                "unresolved": {"type": "array", "items": {"type": "string"}},
                "outline": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["completed", "unresolved", "outline"],
        },
    },
}


PLANNING_INTERNAL_TOOLS: list[dict] = [
    PLANNING_SCOPE_TOOL,
    PLANNING_FACT_STATE_TOOL,
    PLANNING_FINISH_TOOL,
]
PLANNING_TOOLS: list[dict] = [*PLANNING_INTERNAL_TOOLS, *PLANNING_EXTERNAL_TOOLS]


def planning_tools_for_model(planning_model: str) -> list[dict]:
    return [
        *PLANNING_INTERNAL_TOOLS,
        *planning_external_tools_for_model(planning_model),
    ]
