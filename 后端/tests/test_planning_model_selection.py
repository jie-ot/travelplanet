from __future__ import annotations

from unittest import TestCase

from app.ai import orchestrator
from app.ai.clients import vivo_chat_client
from app.core.exceptions import InvalidParamError
from app.models.dto import PlanningRequest
from app.services import planning_service


class PlanningModelSelectionTest(TestCase):
    def test_deepseek_request_uses_official_thinking_parameters(self) -> None:
        runtime = vivo_chat_client._resolve_runtime("deepseek-v4-pro")

        kwargs = vivo_chat_client._completion_request_kwargs(
            runtime=runtime,
            messages=[{"role": "user", "content": "规划武汉到苏州"}],
            temperature=0.2,
            max_completion_tokens=16000,
            timeout=120,
            request_id="local-test",
        )

        self.assertEqual(kwargs["model"], "deepseek-v4-pro")
        self.assertEqual(kwargs["reasoning_effort"], "high")
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertEqual(kwargs["max_tokens"], 16000)
        self.assertNotIn("max_completion_tokens", kwargs)
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("extra_query", kwargs)

    def test_deepseek_tool_turn_replays_reasoning_without_logging_content(self) -> None:
        turn = vivo_chat_client.ChatTurn(
            content="",
            reasoning_content="provider-private-reasoning",
            tool_calls=[
                vivo_chat_client.ToolCall(
                    id="scope",
                    name="declare_trip_scope",
                    arguments="{}",
                )
            ],
        )

        message = orchestrator._assistant_history_message(
            turn, "deepseek-v4-flash"
        )

        self.assertEqual(message["reasoning_content"], "provider-private-reasoning")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "declare_trip_scope")

    def test_conversation_rejects_switching_model(self) -> None:
        request = PlanningRequest.model_validate(
            {
                "message": "继续规划",
                "planningModel": "deepseek-v4-pro",
                "messages": [
                    {
                        "role": "user",
                        "content": "先去苏州",
                        "planningModel": "deepseek-v4-flash",
                    }
                ],
            }
        )

        with self.assertRaises(InvalidParamError):
            planning_service._validate_conversation_model(request)

    def test_old_client_defaults_to_current_doubao(self) -> None:
        request = PlanningRequest(message="去苏州")
        self.assertEqual(request.planning_model, "doubao-seed-2.0-pro")

    def test_api_error_log_details_include_bounded_cause_chain(self) -> None:
        try:
            try:
                raise OSError("TLS handshake failed")
            except OSError as cause:
                raise RuntimeError("connection error") from cause
        except RuntimeError as exc:
            details = vivo_chat_client._api_error_details(exc)

        self.assertEqual(details["error_type"], "RuntimeError")
        self.assertEqual(
            details["error_cause_chain"],
            [{"type": "OSError", "message": "TLS handshake failed"}],
        )
