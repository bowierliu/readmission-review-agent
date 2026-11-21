import json
import shutil
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from app import Handler, ReviewStore
from investigation import CaseData, FIXTURES, investigate
from portfolio_guard import Guard, GuardError, MODEL, initialize
from record_replays import browser_asset
from app import STATIC, load_replay


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
        self.server.live_session = None
        self.ledger = Path(temp.name) / "ledger.db"
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
        with patch.object(self.server, "live_session", side_effect=AssertionError("Live path attempted")):
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

    def guarded_live(self, answers, **limits):
        initialize(self.ledger)
        with closing(sqlite3.connect(self.ledger)) as conn, conn:
            conn.executemany("UPDATE settings SET value=? WHERE name=?",
                             [(n, key) for key, n in {"enabled": 1, **limits}.items()])
        sent = []
        replies = iter(answers)

        def request(action, key, payload, timeout):
            self.assertEqual(key, "offline-placeholder")
            self.assertGreater(timeout, 0)
            sent.append(action)
            if action == "count":
                return {"input_tokens": 100}
            reply = next(replies)
            if isinstance(reply, GuardError):
                raise reply
            return {"model": MODEL, "service_tier": "default", "status": "completed",
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": reply}]}]}
        guard = Guard(self.ledger, key_loader=lambda: "offline-placeholder", request=request)
        self.server.live_session = lambda: guard.investigation("local-owner")
        return sent

    def test_live_reconciliation_hold_survives_later_model_error(self):
        sent = self.guarded_live(['{"check":"reconcile_sources"}', '{"check":"signal_check"}'])
        steps, result = self.run_case(mode="live")
        self.assertEqual(list(steps), ["coverage", "headline", "reconcile_sources"])
        self.assertEqual(result["status"], "data_quality")
        self.assertIn("Hold.", result["verdict"]["title"])
        self.assertIn("source reconciliation failed", result["signal_check"]["label"])
        self.assertIn("invalid check", result["notice"])
        self.assertEqual(sent, ["count", "generate"] * 2)
        with closing(sqlite3.connect(self.ledger)) as conn:
            self.assertEqual(conn.execute("SELECT tools, active FROM runs").fetchone(), (3, 0))

    def test_live_hold_survives_interrupted_provider_response(self):
        self.guarded_live(['{"check":"reconcile_sources"}', GuardError("Provider unavailable.")])
        steps, result = self.run_case(mode="live")
        self.assertEqual(list(steps), ["coverage", "headline", "reconcile_sources"])
        self.assertEqual(result["status"], "data_quality")
        self.assertIn("Provider unavailable", result["notice"])

    def test_guarded_tool_limit_preserves_established_hold(self):
        self.guarded_live(['{"check":"reconcile_sources"}', '{"check":"refresh_notes"}'], tool_calls=3)
        steps, result = self.run_case(mode="live")
        self.assertEqual(list(steps), ["coverage", "headline", "reconcile_sources"])
        self.assertEqual(result["status"], "data_quality")
        self.assertIn("tool limit", result["notice"])

    def test_live_sql_cancels_long_read_and_fails_fast_on_exclusive_lock(self):
        self.guarded_live([], seconds=1)
        # Substitute an expensive read for the first diagnostic in the normal HTTP path.
        with patch("investigation.COVERAGE_SQL",
                   "SELECT count(*) FROM encounters a, encounters b, encounters c"):
            start = time.monotonic()
            _, _, body = self.request("POST", "/api/investigate", {"case_id": "a", "mode": "live"})
            elapsed = time.monotonic() - start
        error = json.loads(body.splitlines()[-1])
        self.assertEqual(error["type"], "error")
        self.assertIn("time limit", error["message"])
        self.assertLess(elapsed, 2.5)

        with closing(sqlite3.connect(self.fixture)) as lock:
            lock.execute("BEGIN EXCLUSIVE")
            start = time.monotonic()
            _, _, body = self.request("POST", "/api/investigate", {"case_id": "a", "mode": "live"})
            self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(json.loads(body.splitlines()[-1])["type"], "error")
        with closing(sqlite3.connect(self.ledger)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM calls").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT sum(active) FROM runs").fetchone()[0], 0)

    def test_http_live_limits_and_coverage_gate_block_provider_calls(self):
        sent = self.guarded_live([], run_cents=1, input_tokens=16_000)
        _, held = self.run_case("c", mode="live")
        self.assertEqual(held["status"], "premature")
        _, _, body = self.request("POST", "/api/investigate", {"case_id": "a", "mode": "live"})
        error = json.loads(body.splitlines()[-1])
        self.assertEqual(error["type"], "error")
        self.assertIn("budget", error["message"])
        self.assertEqual(sent, [])

    def test_static_recordings_match_saved_traces_and_the_local_page_uses_the_api(self):
        self.assertEqual((STATIC / "assets" / "recordings.js").read_text(), browser_asset(load_replay()))
        self.assertIn(b'data-service="pages"', (STATIC / "index.html").read_bytes())
        self.assertIn(b'data-service="local"', self.request("GET", "/")[2])
        self.assertEqual(self.request("GET", "/assets/app.js")[0], 200)

    def test_local_host_and_origin_gate(self):
        with closing(HTTPConnection(*self.server.server_address)) as conn:
            conn.request("GET", "/api/cases", headers={"Host": "unrelated.test"})
            self.assertEqual(conn.getresponse().status, 403)
        with closing(HTTPConnection(*self.server.server_address)) as conn:
            conn.request("POST", "/api/investigate", b'{"case_id":"a","mode":"live"}',
                         {"Origin": "https://unrelated.test", "Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 403)

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
