"""Budget tests use two local accounting clients and an in-memory provider double."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from portfolio_guard import (
    DEFAULTS,
    INPUT_RATE,
    MODEL,
    OUTPUT_RATE,
    Guard,
    GuardError,
    initialize,
)


class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.db"
        initialize(self.path)
        self.set_limit("enabled", 1)
        self.actions: list[str] = []
        self.guard = Guard(self.path, lambda: "test", self.provider)

    def set_limit(self, name: str, value: int) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE settings SET value=? WHERE name=?", (value, name))

    def provider(
        self, action: str, key: str, body: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        self.actions.append(action)
        self.assertGreater(timeout, 0)
        self.assertEqual(body["model"], MODEL)
        self.assertNotIn("tools", body)
        if action == "count":
            return {"input_tokens": 21}
        self.assertIs(body["store"], False)
        self.assertEqual(body["max_output_tokens"], DEFAULTS["output_tokens"])
        return {
            "model": MODEL,
            "status": "completed",
            "service_tier": "default",
            "usage": {"input_tokens": 21, "output_tokens": 5},
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "review"}],
                }
            ],
        }

    def spend(self) -> int:
        with sqlite3.connect(self.path) as db:
            return int(
                db.execute("SELECT coalesce(sum(amount),0) FROM calls").fetchone()[0]
            )

    def test_known_usage_settles_and_missing_ledger_fails_closed(self) -> None:
        with self.guard.investigation("owner") as run:
            self.assertEqual(run.model("restricted", "synthetic case"), "review")
        self.assertEqual(self.spend(), 21 * INPUT_RATE + 5 * OUTPUT_RATE)
        self.path.unlink()
        with self.assertRaises(GuardError), self.guard.investigation("owner"):
            self.fail("missing ledger admitted a run")

    def test_context_cap_rejects_before_inference(self) -> None:
        self.set_limit("input_tokens", 20)
        with self.guard.investigation("owner") as run, self.assertRaises(GuardError):
            run.model("scope", "case")
        self.assertEqual(self.actions, ["count"])

    def test_global_reservation_blocks_racing_clients_and_survives_restart(
        self,
    ) -> None:
        # One full generation fits into one cent. Different people share the global ceiling.
        self.set_limit("input_tokens", 10000)
        self.set_limit("global_cents", 1)
        barrier = threading.Barrier(2)
        entered = threading.Event()
        rejected = threading.Event()

        def unavailable(
            action: str, key: str, body: dict[str, Any], timeout: float
        ) -> dict[str, Any]:
            if action == "count":
                return {"input_tokens": 21}
            self.actions.append(action)
            entered.set()
            rejected.wait(timeout=3)
            raise GuardError("interrupted provider response")

        def simultaneous(person: str) -> None:
            guard = Guard(self.path, lambda: "test", unavailable)
            with guard.investigation(person) as run:
                barrier.wait(timeout=3)
                try:
                    run.model("scope", "case")
                except GuardError as exc:
                    if "budget" in str(exc):
                        rejected.set()

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(simultaneous, ("person-1", "person-2")))
        self.assertTrue(entered.is_set() and rejected.is_set())
        self.assertEqual(self.actions, ["generate"])
        self.assertEqual(self.spend(), 10000 * INPUT_RATE + 384 * OUTPUT_RATE)
        with (
            self.guard.investigation("new-person") as run,
            self.assertRaises(GuardError),
        ):
            run.model("scope", "case")
        self.assertEqual(self.actions, ["generate"])
        with self.assertRaises(GuardError):
            initialize(self.path)

    def test_individual_and_per_run_reservations(self) -> None:
        for setting in ("run_cents", "user_cents"):
            with self.subTest(setting=setting):
                self.set_limit("input_tokens", 20000)
                self.set_limit(setting, 1)
                with (
                    self.guard.investigation("owner") as run,
                    self.assertRaises(GuardError),
                ):
                    run.model("scope", "case")
                self.set_limit(setting, DEFAULTS[setting])
        self.assertEqual(self.actions, [])

    def test_model_tools_runtime_and_disable(self) -> None:
        self.set_limit("tool_calls", 1)
        self.set_limit("model_calls", 1)
        with self.guard.investigation("owner") as run:
            self.assertEqual(run.tool(str, 1), "1")
            with self.assertRaises(GuardError):
                run.tool(str, 2)
            run.model("scope", "case")
            with self.assertRaises(GuardError):
                run.model("scope", "case")
        with self.assertRaises(GuardError), self.guard.investigation("owner") as run:
            run.deadline = time.monotonic() - 1
            run.model("scope", "case")
        with self.assertRaises(GuardError), self.guard.investigation("owner") as run:
            self.set_limit("enabled", 0)
            run.model("scope", "case")
        self.assertEqual(self.actions, ["count", "generate"])

    def test_rate_concurrency(self) -> None:
        self.set_limit("concurrency", 1)
        self.set_limit("starts_per_minute", 1)
        with (
            self.guard.investigation("owner"),
            self.assertRaises(GuardError),
            self.guard.investigation("other"),
        ):
            self.fail("concurrent run admitted")
        with self.assertRaises(GuardError), self.guard.investigation("other"):
            self.fail("rate limit reset")

    def test_mismatched_final_usage_disables_all_clients(self) -> None:
        def bad_usage(
            action: str, key: str, body: dict[str, Any], timeout: float
        ) -> dict[str, Any]:
            result = self.provider(action, key, body, timeout)
            if action == "generate":
                result["usage"]["output_tokens"] = 385
            return result

        guard = Guard(self.path, lambda: "test", bad_usage)
        with self.assertRaises(GuardError), guard.investigation("owner") as run:
            run.model("scope", "case")
        with self.assertRaises(GuardError), self.guard.investigation("other"):
            self.fail("unexpected pricing did not trip disable switch")
