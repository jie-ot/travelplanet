from __future__ import annotations

import json
from unittest import TestCase
from unittest.mock import patch

from app.ai import orchestrator
from app.ai.clients.vivo_chat_client import ChatTurn, ToolCall


class PlanningProtocolTest(TestCase):
    def test_scope_fact_state_finish_then_final_and_audit(self) -> None:
        itinerary = {
            "trip_info": {
                "destination": "苏州",
                "start_date": "2026-08-01",
                "end_date": "2026-08-01",
                "date_label": "8月1日",
            },
            "experience_summary": {
                "tripTheme": "园林慢游",
                "pace": "舒适",
                "intensity": 46,
                "highlights": ["拙政园"],
                "weatherSummary": "天气以临行前预报为准",
                "personalizationTags": ["人文优先"],
            },
            "preparations": [],
            "bookings": [],
            "food_recommendations": [],
            "itinerary": [],
        }
        turns = iter(
            [
                ChatTurn(
                    tool_calls=[
                        ToolCall(
                            id="scope",
                            name="declare_trip_scope",
                            arguments=json.dumps(
                                {
                                    "origin": "武汉",
                                    "destinations": ["苏州"],
                                    "startDate": "2026-08-01",
                                    "endDate": "2026-08-01",
                                    "needsTransport": True,
                                    "needsHotel": False,
                                    "interests": ["园林"],
                                    "uncertainties": [],
                                },
                                ensure_ascii=False,
                            ),
                        ),
                        ToolCall(
                            id="poi",
                            name="amap_poi_search",
                            arguments='{"keyword":"拙政园","city":"苏州"}',
                        ),
                    ]
                ),
                ChatTurn(
                    tool_calls=[
                        ToolCall(
                            id="finish",
                            name="finish_research",
                            arguments='{"completed":["主要景点"],"unresolved":[],"outline":["园林慢游"]}',
                        )
                    ]
                ),
                ChatTurn(content=json.dumps(itinerary, ensure_ascii=False)),
                ChatTurn(
                    content='{"passed":true,"missingQueries":[],"problems":[]}'
                ),
            ]
        )
        external_calls: list[str] = []
        business_events: list[tuple[str, dict]] = []

        def fake_chat_messages(**_kwargs) -> ChatTurn:
            return next(turns)

        def fake_execute(tool_name: str, _arguments: dict) -> dict:
            external_calls.append(tool_name)
            return {
                "tool": tool_name,
                "status": "ok",
                "pois": [
                    {
                        "status": "ok",
                        "name": "拙政园",
                        "address": "苏州市姑苏区",
                        "location": "120.629,31.324",
                        "category": "风景名胜",
                    }
                ],
            }

        with (
            patch.object(
                orchestrator.vivo_chat_client,
                "chat_messages",
                side_effect=fake_chat_messages,
            ),
            patch.object(
                orchestrator,
                "log_event",
                side_effect=lambda event, **fields: business_events.append(
                    (event, fields)
                ),
            ),
        ):
            result = orchestrator._plan_with_tools("system", "user", fake_execute)

        self.assertEqual(result.trip_info.destination, "苏州")
        self.assertEqual(external_calls, ["amap_poi_search"])
        round_results = [
            fields
            for event, fields in business_events
            if event == "planning_tool_round_result"
        ]
        self.assertEqual(round_results[0]["tool_names"], [
            "declare_trip_scope",
            "amap_poi_search",
        ])
        tool_results = [
            fields
            for event, fields in business_events
            if event == "planning_execute_tool"
            and fields.get("status") != "start"
        ]
        poi_result = next(
            item for item in tool_results if item["tool_name"] == "amap_poi_search"
        )
        self.assertEqual(poi_result["issued_fact_count"], 1)
        self.assertFalse(poi_result["cache_hit"])
        finish_result = next(
            item for item in tool_results if item["tool_name"] == "finish_research"
        )
        self.assertTrue(finish_result["research_finished"])
