"""Contract tests for the model adapter, with the network replaced by a response fixture."""

import io
import json
import unittest
from unittest.mock import patch

from investigation import CaseData, ToolSelectionError, investigate
from live import make_chooser


def completion(name=None, arguments="{}"):
    calls = ([{"id": "call_1", "type": "function", "function": {
        "name": name, "arguments": arguments,
    }}] if name else [])
    return io.BytesIO(json.dumps({"choices": [{
        "finish_reason": "tool_calls" if calls else "stop",
        "message": {"role": "assistant", "content": None, "tool_calls": calls},
    }]}).encode())


class LiveAdapterTests(unittest.TestCase):
    def test_live_model_selects_tools_from_aggregate_evidence(self):
        with patch("live.urlopen", side_effect=[
            completion("hospital_counts"),
            completion("reconcile_sources"),
            completion("refresh_notes"),
        ]) as transport:
            events = list(investigate(CaseData("a"), make_chooser("test-key", "test-model")))

        self.assertEqual(events[-1]["result"]["status"], "data_quality")
        self.assertEqual(events[-2]["step"]["id"], "refresh_notes")
        self.assertEqual(transport.call_count, 3)
        requests = [call.args[0] for call in transport.call_args_list]
        request = requests[-1]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(request.data)
        self.assertEqual([tool["function"]["name"] for tool in payload["tools"]], ["refresh_notes"])
        evidence = payload["messages"][-1]["content"]
        self.assertIn("Mesa Regional Hospital", evidence)
        self.assertNotIn("Case A", evidence)
        self.assertNotIn("scenario_id", evidence)
        self.assertNotIn("encounter_id", evidence)
        self.assertNotIn("patient_id", evidence)
        self.assertNotIn("test-key", evidence)

    def test_live_model_cannot_pass_unexpected_tool_arguments(self):
        with patch("live.urlopen", return_value=completion("hospital_counts", '{"sql":"DELETE"}')):
            with self.assertRaises(ToolSelectionError):
                list(investigate(CaseData("a"), make_chooser("test-key", "test-model")))
