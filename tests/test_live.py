"""Model selection and evidence contracts. The provider is replaced by a test double."""

import json
import unittest
from unittest.mock import Mock

from investigation import CaseData, ToolSelectionError, investigate
from live import make_chooser


class LiveAdapterTests(unittest.TestCase):
    def test_live_model_selects_tools_from_aggregate_evidence(self):
        run = Mock()
        run.model.side_effect = [json.dumps({"check": key}) for key in
                                 ("hospital_counts", "reconcile_sources", "refresh_notes")]
        events = list(investigate(CaseData("a"), make_chooser(run)))
        self.assertEqual(events[-1]["result"]["status"], "data_quality")
        self.assertEqual(events[-2]["step"]["id"], "refresh_notes")
        self.assertEqual(run.model.call_count, 3)
        prompt = run.model.call_args.args[1]
        self.assertEqual(list(json.loads(prompt)["available_checks"]), ["refresh_notes"])
        self.assertIn("Mesa Regional Hospital", prompt)
        for forbidden in ("Case A", "scenario_id", "encounter_id", "patient_id", "mesa-2026-02-026"):
            self.assertNotIn(forbidden, prompt)

    def test_live_model_cannot_pass_unexpected_tool_arguments(self):
        for answer in ('{"check":"hospital_counts","sql":"write"}', '{"check":"unknown"}',
                       '{"check":["hospital_counts"]}', 'null', 'unstructured'):
            with self.subTest(answer=answer), self.assertRaises(ToolSelectionError):
                make_chooser(Mock(model=Mock(return_value=answer)))({"hospital_counts": "count"}, ())

    def test_stop_is_valid(self):
        run = Mock(model=Mock(return_value='{"check":null}'))
        self.assertIsNone(make_chooser(run)({"hospital_counts": "count"}, ()))
