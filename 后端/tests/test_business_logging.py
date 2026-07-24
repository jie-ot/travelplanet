from __future__ import annotations

import json
from unittest import TestCase
from unittest.mock import mock_open, patch

from app.core import business_logging


class BusinessLoggingTest(TestCase):
    def test_failure_contains_ordering_and_traceback_diagnostics(self) -> None:
        writer = mock_open()
        with (
            patch.object(
                business_logging,
                "_build_log_path",
                return_value="unused-business.log",
            ),
            patch("builtins.open", writer),
            self.assertRaisesRegex(ValueError, "diagnostic failure"),
        ):
            with business_logging.business_task("test_logging"):
                business_logging.log_event(
                    "existing_stage",
                    status="start",
                    detail={"fact_id": "fact_demo"},
                )
                raise ValueError("diagnostic failure")
        rows = [
            json.loads(call.args[0])
            for call in writer().write.call_args_list
        ]

        self.assertEqual([row["sequence"] for row in rows], [1, 2, 3])
        self.assertEqual(rows[1]["event"], "existing_stage")
        failure = rows[-1]
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(failure["error_type"], "ValueError")
        self.assertIn("diagnostic failure", failure["traceback"])
        self.assertIn("pid", failure)
        self.assertIn("thread", failure)
