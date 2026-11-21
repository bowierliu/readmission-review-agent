"""Bounded exhaustive check of the real portfolio ledger, without any network or credentials."""

import itertools
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portfolio_guard import MODEL, Guard, GuardError, initialize

# Standard rates in billionths of a US dollar. Independently calculated for this test.
RESERVATION = 9_228_000  # 10,000 input tokens and 384 output tokens
KNOWN = 6_795_000  # 9,000 input tokens and 10 output tokens
CENT = 10_000_000


def policy(path, user, total):
    initialize(path)
    with sqlite3.connect(path) as db:
        db.executemany("UPDATE settings SET value=? WHERE name=?", [
            (1, "enabled"), (10_000, "input_tokens"), (1, "run_cents"),
            (user, "user_cents"), (total, "global_cents"),
        ])


def inspect_amount(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT coalesce(sum(amount),0) FROM calls").fetchone()[0]


def check_schedule(path, steps, user_limit, global_limit):
    policy(path, user_limit, global_limit)
    charged = {0: 0, 1: 0}
    for user, loss in steps:
        before = sum(charged.values())
        allowed = before + RESERVATION <= global_limit*CENT and charged[user] + RESERVATION <= user_limit*CENT
        actions = []

        def provider(action, key, body, timeout):
            # A second connection must observe the full durable reservation even before counting.
            assert allowed, f"provider contacted over budget: {steps}"
            assert inspect_amount(path) == before + RESERVATION
            actions.append(action)
            if action == "count":
                return {"input_tokens": 9000}
            if loss:
                raise GuardError("simulated lost response")
            return {"model": MODEL, "service_tier":"default", "status":"completed",
                    "usage":{"input_tokens":9000,"output_tokens":10},
                    "output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}]}

        try:
            with Guard(path, lambda: "mock", provider).investigation(str(user)) as run:
                assert run.model("test", "synthetic") == "ok"
            assert allowed and not loss
        except GuardError:
            assert not allowed or loss
        assert actions == (["count", "generate"] if allowed else [])
        if allowed:
            charged[user] += RESERVATION if loss else KNOWN
        assert inspect_amount(path) == sum(charged.values())


def crash_child(path):
    def provider(action, key, body, timeout):
        if action == "count":
            return {"input_tokens": 12}
        # Simulate a killed app after dispatch but before settlement or context-manager cleanup.
        os._exit(73)
    with Guard(path, lambda: "mock", provider).investigation("user") as run:
        run.model("synthetic", "test")


def crash_check(path):
    policy(path, 1, 1)
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--crash", str(path)])
    assert proc.returncode == 73
    assert inspect_amount(path) == RESERVATION
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT sum(active) FROM runs").fetchone() == (1,)

    def rejected(*args):
        raise AssertionError("a restarted client reached the provider after a crash")
    try:
        with Guard(path, lambda: "mock", rejected).investigation("another operator") as run:
            run.model("synthetic", "retry")
    except GuardError as error:
        assert "budget" in str(error)
    else:
        raise AssertionError("crash released a reservation")
    assert inspect_amount(path) == RESERVATION


def main():
    if not __debug__:
        raise RuntimeError("Run verification without Python's -O option.")
    if len(sys.argv) == 3 and sys.argv[1] == "--crash":
        crash_child(Path(sys.argv[2]))
        return
    actions = list(itertools.product(range(2), (False, True)))
    with TemporaryDirectory() as directory:
        root = Path(directory)
        count = 0
        for user_limit, global_limit in ((1,2), (2,1)):
            for steps in itertools.product(actions, repeat=3):
                check_schedule(root / f"case-{count}.db", steps, user_limit, global_limit)
                count += 1
        crash_check(root / "crash.db")
    print(f"{count} bounded schedules passed; abrupt process exit and retry passed.")


if __name__ == "__main__":
    main()
