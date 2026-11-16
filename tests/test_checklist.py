import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from http.client import HTTPConnection, IncompleteRead
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from app import Handler, ReviewStore
from investigation import CaseData, FIXTURES, investigate
from live import make_chooser


class QuietHandler(Handler):
    def log_message(self, *_):
        pass


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.fixture = Path(temp.name) / "fixtures.sqlite3"
        shutil.copyfile(FIXTURES, self.fixture)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.server.fixture_path = self.fixture
        self.server.choose = None
        self.server.reviews = ReviewStore(Path(temp.name) / "reviews.sqlite3")
        self.addCleanup(self.server.server_close)
        thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None):
        with closing(HTTPConnection(*self.server.server_address, timeout=5)) as conn:
            conn.request(method, path, json.dumps(body) if body is not None else None,
                         {"Content-Type": "application/json"} if body is not None else {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def run_case(self, case_id="a", **options):
        status, headers, payload = self.request(
            "POST", "/api/investigate", {"case_id": case_id, **options})
        self.assertEqual(status, 200, payload)
        self.assertEqual(headers["Content-Type"], "application/x-ndjson")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        events = [json.loads(line) for line in payload.splitlines()]
        self.assertEqual(events[-1]["type"], "complete")
        return {event["step"]["id"]: event["step"] for event in events[:-1]}, events[-1]["result"]

    def test_default_checklist_matches_saved_case_a_without_network(self):
        with patch("live.urlopen", side_effect=AssertionError("Network attempted")):
            fresh, result = self.run_case()
            replay, saved_result = self.run_case(mode="replay")
        self.assertEqual(result["status"], saved_result["status"])
        self.assertEqual(result["status"], "data_quality")
        self.assertEqual(result["draft"], saved_result["draft"])
        for run, mode in ((fresh, "checklist"), (replay, "replay")):
            self.assertEqual(list(run), ["coverage", "headline", "hospital_counts",
                                        "reconcile_sources", "refresh_notes"])
            for key, check in run.items():
                self.assertEqual(check["mode"], mode)
                self.assertEqual(check["saved_trace"], mode == "replay")
                self.assertTrue(check["source"])
                self.assertTrue(check["ran_at"])
                self.assertEqual(check["row_count"], len(check["rows"]))
                self.assertEqual(check["rows"], replay[key]["rows"])

    def test_fixture_mutation_changes_fresh_counts_only_and_rerun_has_new_timestamp(self):
        saved_before, _ = self.run_case(mode="replay")
        fresh_before, _ = self.run_case()
        with closing(sqlite3.connect(self.fixture)) as conn, conn:
            conn.execute("INSERT INTO reporting_index VALUES (?, ?)", ("a", "mesa-2026-02-026-index"))
        fresh_after, _ = self.run_case()
        self.assertEqual(fresh_before["headline"]["rows"][-1][2], 125)
        self.assertEqual(fresh_after["headline"]["rows"][-1][2], 126)
        self.assertNotEqual(fresh_before["headline"]["ran_at"], fresh_after["headline"]["ran_at"])
        saved_after, _ = self.run_case(mode="replay")
        self.assertEqual(saved_before, saved_after)

    def test_gap_drilldown_has_exact_missing_stays_and_selection_breakdown(self):
        steps, _ = self.run_case()
        gap, = steps["reconcile_sources"]["drilldowns"]
        self.assertEqual(gap["row_count"], 75)
        self.assertEqual(len({row[0] for row in gap["rows"]}), 75)
        self.assertTrue(all(row[1:] == ["Mesa Regional Hospital", "2026-02", 0]
                            for row in gap["rows"]))
        self.assertEqual(gap["non_readmitted"], {"eligible": 90, "missing": 75})
        self.assertEqual(gap["readmitted"], {"eligible": 10, "kept": 10})
        self.assertEqual(self.run_case("b")[0]["reconcile_sources"]["drilldowns"], [])
        self.assertNotIn("reconcile_sources", self.run_case("c")[0])

    def test_sql_disclosure_uses_exact_executed_statements_and_sql_row_counts(self):
        data = CaseData("a", self.fixture)
        with patch.object(data, "query", wraps=data.query) as query:
            events = list(investigate(data))
        executed = {call.args[0]: call.kwargs for call in query.call_args_list}
        tables = [event["step"] for event in events if event["type"] == "step"]
        tables.extend(next(step for step in tables if step["id"] == "reconcile_sources")["drilldowns"])
        for table in tables:
            with self.subTest(table=table.get("id", "drilldown")):
                self.assertIn(table["sql"], executed)
                sql_rows = data.query(table["sql"], **executed[table["sql"]])
                self.assertEqual(table["sql_row_count"], len(sql_rows))

    def test_verdict_payload_supports_full_and_compare_views_from_fresh_results(self):
        _, case_a = self.run_case()
        _, case_b = self.run_case("b")
        _, case_c = self.run_case("c")
        expected = {
            "a": ("Hold. Do not publish 16.0%.", "Send to data team."),
            "b": ("Counts reconcile. Send to quality review.", "Send to quality team."),
            "c": ("Wait. February is not complete.", "Wait for complete follow-up."),
        }
        for result in (case_a, case_b, case_c):
            with self.subTest(case=result["card"]["id"]):
                verdict = result["verdict"]
                self.assertEqual((verdict["title"], verdict["next_action"]),
                                 expected[result["card"]["id"]])
                self.assertTrue(verdict["support"])
                self.assertTrue(verdict["reason"])
        for result in (case_a, case_b):
            self.assertEqual(result["card"]["prior"]["rate"], 0.10)
            self.assertEqual(result["card"]["current"]["rate"], 0.16)
            self.assertEqual(result["mode"], "checklist")
        self.assertEqual(
            case_a["verdict"]["support"],
            "Mesa's reporting index is missing 75 of 90 non-readmitted stays while keeping all "
            "10 readmitted stays, which inflates the rate. Send to data team for reconciliation.",
        )
        self.assertEqual(
            case_b["verdict"]["support"],
            "Both sources agree. The change is +6.0 pp with an interval that includes zero overall; "
            "Mesa shows the stronger signal. Observed, unadjusted, synthetic data.",
        )
        self.assertEqual(
            case_c["verdict"]["support"],
            "Data through Mar 12 completes the 30-day window only for discharges through Feb 10; "
            "February needs coverage through Mar 30. Re-run when follow-up is complete.",
        )
        self.assertIn("75 Mesa stays", case_a["verdict"]["reason"])
        self.assertIn("-0.6 to +12.7 pp", case_b["verdict"]["reason"])
        self.assertIn("through Feb 10", case_c["verdict"]["support"])
        self.assertTrue(case_c["card"]["preliminary"])

        with closing(sqlite3.connect(self.fixture)) as conn, conn:
            conn.execute("INSERT INTO reporting_index VALUES (?, ?)", ("a", "mesa-2026-02-026-index"))
        _, changed = self.run_case()
        self.assertIn("15.9%", changed["verdict"]["title"])
        self.assertIn("74 Mesa stays", changed["verdict"]["reason"])
        self.assertNotEqual(case_a["card"]["current"], changed["card"]["current"])

    def test_completed_run_can_be_marked_reviewed_from_the_http_api(self):
        _, result = self.run_case()
        body = {"run_id": result["run_id"]}
        status, _, response = self.request("POST", "/api/review", body)
        self.assertEqual(status, 200)
        review = json.loads(response)
        self.assertTrue(review["reviewed"])
        reloaded = ReviewStore(self.server.reviews.path)
        self.assertEqual(reloaded.mark_reviewed(result["run_id"]), review)

    def test_live_reconciliation_hold_survives_later_model_error(self):
        selections = iter(("reconcile_sources", "signal_check"))
        self.server.choose = lambda *_: next(selections)
        steps, result = self.run_case(mode="live")
        self.assertEqual(list(steps), ["coverage", "headline", "reconcile_sources"])
        self.assertEqual(result["status"], "data_quality")
        self.assertIn("Hold.", result["verdict"]["title"])
        self.assertIn("source reconciliation failed", result["signal_check"]["label"])
        self.assertIn("unavailable check", result["notice"])

    def test_live_hold_survives_malformed_and_interrupted_provider_responses(self):
        self.server.choose = make_chooser("test-key", "test-model")
        reconciles = {
            "choices": [{"finish_reason": "tool_calls", "message": {
                "tool_calls": [{"function": {"name": "reconcile_sources", "arguments": "{}"}}],
            }}],
        }
        stopped_with_wrong_shape = {
            "choices": [{"finish_reason": "stop", "message": {"tool_calls": {}}}],
        }
        failures = (
            ("null message", io.BytesIO(b'{"choices":[{"finish_reason":"tool_calls","message":null}]}')),
            ("malformed tool calls", io.BytesIO(json.dumps(stopped_with_wrong_shape).encode())),
            ("invalid UTF-8", io.BytesIO(b'{"choices":"' + bytes([0xFF]) + b'"}')),
            ("interrupted HTTP body", IncompleteRead(b"partial")),
            ("connection reset", ConnectionResetError("connection reset")),
        )
        for description, failure in failures:
            with self.subTest(description=description):
                first = io.BytesIO(json.dumps(reconciles).encode())
                with patch("live.urlopen", side_effect=[first, failure]):
                    steps, result = self.run_case(mode="live")
                self.assertEqual(list(steps), ["coverage", "headline", "reconcile_sources"])
                self.assertEqual(result["status"], "data_quality")
                self.assertIn("reconciliation failed", result["signal_check"]["label"])
                self.assertIn("The live model", result["notice"])

    def test_invalid_inputs_and_private_files_are_rejected(self):
        for body in (
            {"case_id": []}, {"case_id": "z"}, {"case_id": "small"}, {"case_id": "a", "mode": "SELECT 1"},
            {"case_id": "a", "tool": "arbitrary"}, {"case_id": "a", "sql": "SELECT 1"},
        ):
            self.assertEqual(self.request("POST", "/api/investigate", body)[0], 400)
        self.assertEqual(self.request("POST", "/api/investigate?tool=arbitrary", {"case_id": "a"})[0], 400)
        self.assertEqual(self.request("POST", "/api/investigate", {"case_id": "a", "mode": "live"})[0], 409)
        self.assertEqual(self.request("POST", "/api/review", {"run_id": "missing"})[0], 404)
        for path in ("/app.py", "/sql/metric.sql", "/data/fixtures.sqlite3", "/static/../app.py"):
            self.assertEqual(self.request("GET", path)[0], 404)
