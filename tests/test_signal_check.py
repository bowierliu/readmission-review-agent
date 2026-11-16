import ast
import unittest
from unittest.mock import patch

from investigation import CaseData, ToolSelectionError, investigate, record_with_local_policy
from signal_stats import fisher_exact_two_sided, newcombe_diff_interval, wilson_interval


def run(case_id, choose=None):
    events = list(investigate(CaseData(case_id), choose))
    return [event["step"] for event in events[:-1]], events[-1]["result"]


class IntervalTests(unittest.TestCase):
    def test_wilson_bounds_stay_in_unit_interval(self):
        for n in (1, 12, 100):
            for k in range(n + 1):
                lo, hi = wilson_interval(k, n)
                self.assertLessEqual(0, lo)
                self.assertLessEqual(lo, k / n)
                self.assertLessEqual(k / n, hi)
                self.assertLessEqual(hi, 1)
        self.assertEqual(wilson_interval(0, 100)[0], 0)
        self.assertEqual(wilson_interval(100, 100)[1], 1)

    def test_newcombe_matches_published_example_and_reverses_with_arm_order(self):
        lo, hi = newcombe_diff_interval(3, 10, 9, 10)
        self.assertAlmostEqual(lo, 0.1705, places=4)
        self.assertAlmostEqual(hi, 0.8090, places=4)
        reverse_lo, reverse_hi = newcombe_diff_interval(9, 10, 3, 10)
        self.assertAlmostEqual(reverse_lo, -hi)
        self.assertAlmostEqual(reverse_hi, -lo)

    def test_fisher_probability_ordered_includes_tied_tables(self):
        self.assertAlmostEqual(fisher_exact_two_sided(2, 6, 0, 6), 420 / 924)
        self.assertEqual(fisher_exact_two_sided(0, 6, 0, 6), 1.0)
        with self.assertRaises(ValueError):
            wilson_interval(7, 6)

    def test_stats_module_imports_only_math(self):
        import signal_stats
        with open(signal_stats.__file__) as source:
            tree = ast.parse(source.read())
        self.assertTrue(all(node.module == "math" for node in ast.walk(tree)
                            if isinstance(node, ast.ImportFrom)))
        self.assertTrue(all(alias.name == "math" for node in ast.walk(tree)
                            if isinstance(node, ast.Import) for alias in node.names))


class SignalCheckTests(unittest.TestCase):
    def test_checklist_reproduces_case_b_reference_values(self):
        checks, result = run("b")
        ids = [check["id"] for check in checks]
        self.assertLess(ids.index("reconcile_sources"), ids.index("signal_check"))
        self.assertEqual(result["status"], "observed_increase")
        self.assertEqual(result["draft"]["owner"], "Quality team")
        expected = {
            "Overall": ((20, 200), (32, 200), 6.0, (-0.64, 12.67), 0.101),
            "Mesa Regional Hospital": ((10, 100), (20, 100), 10.0, (0.01, 19.95), 0.073),
            "Harbor Medical Center": ((10, 100), (12, 100), 2.0, (-6.96, 11.00), 0.82),
        }
        scopes = result["signal_check"]["scopes"]
        self.assertEqual(len(scopes), len(expected))
        for scope in scopes:
            name = scope["scope"]
            older, newer, change, interval, p_value = expected[name]
            for side, (k, n) in zip(("prior", "current"), (older, newer)):
                arm = scope[side]
                self.assertEqual((arm["readmissions"], arm["discharges"]), (k, n))
                self.assertEqual(arm["rate"], k / n)
                self.assertEqual(tuple(arm["wilson"]), wilson_interval(k, n))
            self.assertAlmostEqual(scope["change_pp"], change, delta=0.05)
            for measured, target in zip(scope["interval_pp"], interval):
                self.assertAlmostEqual(measured, target, delta=0.05)
            self.assertAlmostEqual(scope["fisher_p"], p_value, delta=0.002)
            expected_signal = ("Difference is outside chance variation (unadjusted)" if name.startswith("Mesa")
                               else "Consistent with chance variation (unadjusted)")
            self.assertEqual(scope["signal"], expected_signal)

    def test_small_and_zero_event_fixtures_show_counts_with_low_event_label(self):
        for case_id, name, count in (("small", "Overall", (2, 12)),
                                     ("zero", "Mesa Regional Hospital", (0, 100))):
            with self.subTest(case_id=case_id):
                _, result = run(case_id)
                scope = next(row for row in result["signal_check"]["scopes"] if row["scope"] == name)
                arm = scope["current"]
                self.assertEqual((arm["readmissions"], arm["discharges"]), count)
                self.assertEqual(scope["signal"], "Too few events to interpret")
                self.assertTrue(scope["low_events"])
                self.assertIsNotNone(scope["fisher_p"])
                self.assertIsNotNone(scope["interval_pp"])

    def test_blocked_or_unselected_signal_never_runs(self):
        with patch.object(CaseData, "signal_check", side_effect=AssertionError("Computed despite gate")):
            for case_id in ("c", "incomplete"):
                checks, result = run(case_id)
                self.assertEqual([check["id"] for check in checks], ["coverage"])
                self.assertEqual(result["status"], "premature")
                self.assertEqual(result["signal_check"]["label"], "Not computed: follow-up incomplete")
            checks, result = run("a")
            self.assertNotIn("signal_check", [check["id"] for check in checks])
            self.assertEqual(result["status"], "data_quality")
            self.assertIn("reconciliation", result["signal_check"]["label"])
            _, cautious = run("b", lambda *_: None)
            self.assertEqual(cautious["status"], "needs_review")
            self.assertIn("reconciliation", cautious["signal_check"]["label"])

    def test_model_can_request_signal_only_after_reconciliation_passes(self):
        with self.assertRaises(ToolSelectionError):
            run("b", lambda *_: "signal_check")

        def wants_early_signal(choices, evidence):
            if "reconcile_sources" in choices:
                return "reconcile_sources"
            self.assertNotIn("signal_check", choices)
            return "signal_check"

        steps, held = run("a", wants_early_signal)
        self.assertEqual(steps[-1]["id"], "reconcile_sources")
        self.assertEqual(held["status"], "data_quality")
        self.assertIn("reconciliation failed", held["signal_check"]["label"])
        self.assertIn("unavailable check", held["notice"])

        def provider_stops_after_gap(choices, evidence):
            if "reconcile_sources" in choices:
                return "reconcile_sources"
            raise ToolSelectionError("The live model is unavailable.")

        _, held = run("a", provider_stops_after_gap)
        self.assertEqual(held["status"], "data_quality")
        self.assertIn("unavailable", held["notice"])
        with self.assertRaises(ToolSelectionError):
            run("b", provider_stops_after_gap)

        selections = iter(("reconcile_sources", "signal_check", None))
        steps, result = run("b", lambda choices, evidence: next(selections))
        self.assertEqual(steps[-1]["id"], "signal_check")
        self.assertEqual(result["status"], "needs_review")
        _, recorded = run("b", record_with_local_policy)
        self.assertEqual(recorded["status"], "observed_increase")
        self.assertFalse(recorded["signal_check"]["scopes"])
