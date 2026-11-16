import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from investigation import CaseData, ToolSelectionError, investigate, record_with_local_policy
from app import ReviewStore, read_replay


class InvestigationTests(unittest.TestCase):
    def run_case(self, case_id):
        events = list(investigate(CaseData(case_id), record_with_local_policy))
        steps = {event["step"]["id"]: event["step"] for event in events[:-1]}
        return steps, events[-1]["result"]

    def test_equal_headline_rates_have_different_numerators_and_denominators(self):
        expected = {
            "a": ((20, 200, 0.10), (20, 125, 0.16)),
            "b": ((20, 200, 0.10), (32, 200, 0.16)),
        }
        for case_id, counts in expected.items():
            with self.subTest(case_id=case_id):
                card = CaseData(case_id).card()
                metrics = ("readmissions", "discharges", "rate")
                actual = tuple(tuple(card[period][key] for key in metrics)
                               for period in ("prior", "current"))
                self.assertEqual(actual, counts)

    def test_denominator_loss_localizes_to_mesa_and_checks_refresh(self):
        steps, result = self.run_case("a")
        self.assertEqual(list(steps), [
            "coverage", "headline", "hospital_counts", "reconcile_sources", "refresh_notes",
        ])
        mesa = steps["reconcile_sources"]["rows"][-1]
        self.assertEqual(mesa[:2], ["2026-02", "Mesa Regional Hospital"])
        self.assertEqual(mesa[2:], [10, 25, 10, 100, 0, 75])
        self.assertEqual(result["status"], "data_quality")
        self.assertEqual(result["draft"]["owner"], "Data team")
        self.assertIn("75", result["draft"]["finding"])
        self.assertIn("Mesa Regional Hospital", result["draft"]["finding"])

    def test_observed_increase_reconciles_and_localizes_the_additional_returns(self):
        steps, result = self.run_case("b")
        self.assertEqual(list(steps), [
            "coverage", "headline", "hospital_counts", "reconcile_sources",
        ])
        self.assertTrue(all(row[-2:] == [0, 0]
                            for row in steps["reconcile_sources"]["rows"]))
        self.assertEqual(result["status"], "observed_increase")
        self.assertEqual(result["draft"]["owner"], "Quality team")
        self.assertIn("10 of 12", result["draft"]["finding"])
        self.assertIn("Mesa Regional Hospital", result["draft"]["finding"])
        self.assertTrue(result["draft"]["open_questions"])

    def test_incomplete_followup_stops_before_any_model_call(self):
        def must_not_run(*_):
            self.fail("The mandatory follow-up gate should prevent model calls")

        case_data = CaseData("c")
        card = case_data.card()
        self.assertTrue(card["preliminary"])
        self.assertEqual(card["current"], {"readmissions": 12, "discharges": 200, "rate": 0.06})
        events = list(investigate(case_data, must_not_run))
        self.assertEqual([event["type"] for event in events], ["step", "complete"])
        self.assertEqual(events[0]["step"]["id"], "coverage")
        self.assertEqual(events[-1]["result"]["status"], "premature")
        self.assertIn("2026-03-30", events[-1]["result"]["draft"]["next_action"])

    def test_followup_gate_requires_the_full_window_at_both_hospitals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.sqlite3"
            shutil.copyfile(CaseData("c").path, path)
            with closing(sqlite3.connect(path)) as conn:
                with conn:
                    conn.execute("""
                        UPDATE source_coverage SET observed_through = '2026-03-30'
                        WHERE scenario_id = 'c' AND hospital_id = 'harbor'
                    """)
                self.assertTrue(CaseData("c", path).card()["preliminary"])
                with conn:
                    conn.execute("""
                        UPDATE source_coverage SET observed_through = '2026-03-30'
                        WHERE scenario_id = 'c' AND hospital_id = 'mesa'
                    """)
                self.assertFalse(CaseData("c", path).card()["preliminary"])

    def test_agent_receives_aggregate_evidence_and_can_choose_order(self):
        def choose(choices, steps):
            self.assertNotIn("Case B", json.dumps(steps))
            self.assertNotIn("scenario_id", json.dumps(steps))
            if "reconcile_sources" in choices:
                return "reconcile_sources"
            if "hospital_counts" in choices:
                self.assertEqual(steps[-1]["id"], "reconcile_sources")
                return "hospital_counts"
            return None

        events = list(investigate(CaseData("b"), choose))
        self.assertEqual([event["step"]["id"] for event in events[:-1]], [
            "coverage", "headline", "reconcile_sources", "hospital_counts",
        ])
        self.assertEqual(events[-1]["result"]["status"], "observed_increase")

    def test_agent_cannot_repeat_checks_or_clear_an_unreconciled_increase(self):
        events = list(investigate(CaseData("b"), lambda *_: None))
        self.assertEqual(events[-1]["result"]["status"], "needs_review")
        with self.assertRaises(ToolSelectionError):
            list(investigate(CaseData("b"), lambda *_: "hospital_counts"))

    def test_diagnostics_use_read_only_sqlite(self):
        with closing(CaseData("a").connect()) as conn, self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM encounters")

    def test_saved_replays_match_current_sql_evidence_and_drafts(self):
        for case_id in ("a", "b", "c"):
            with self.subTest(case_id=case_id):
                self.assertEqual(
                    list(read_replay(case_id)),
                    list(investigate(CaseData(case_id), record_with_local_policy)),
                )

    def test_review_action_persists_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.sqlite3"
            store = ReviewStore(path)
            run_id = store.completed()
            self.assertIsNone(store.mark_reviewed("not-a-run"))
            marked = store.mark_reviewed(run_id)
            self.assertEqual(ReviewStore(path).mark_reviewed(run_id), marked)
            self.assertTrue(marked["reviewed"])
            self.assertTrue(marked["reviewed_at"])


if __name__ == "__main__":
    unittest.main()
