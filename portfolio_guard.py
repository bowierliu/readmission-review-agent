"""Local operator controls and durable, shared accounting for the portfolio demos."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

MODEL = "gpt-5.4-mini-2026-03-17"
# Standard global text endpoint, USD billionths per token (full uncached price).
INPUT_RATE, OUTPUT_RATE = 750, 4500
DEFAULTS = {
    "input_tokens": 4096,
    "output_tokens": 384,
    "model_calls": 8,
    "tool_calls": 16,
    "seconds": 90,
    "run_cents": 3,
    "user_cents": 10,
    "global_cents": 25,
    "starts_per_minute": 6,
    "concurrency": 2,
}
P = ParamSpec("P")
T = TypeVar("T")


class GuardError(Exception):
    """Safe to report to a user; never includes provider or credential content."""


def _state() -> Path:
    return Path.home() / ".healthcare-portfolio" / "ledger.sqlite3"


def _key() -> str:
    key = os.environ.get("PORTFOLIO_OPENAI_KEY", "").strip()
    if not key and sys.platform == "darwin":
        try:
            result = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-s",
                    "Healthcare Portfolio",
                    "-a",
                    "openai",
                    "-w",
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                key = result.stdout.decode().strip()
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            pass
    if not key:
        raise GuardError(
            "Configure the portfolio key in the local credential store or environment."
        )
    return key


@contextmanager
def _transaction(path: Path, *, create: bool = False) -> Iterator[sqlite3.Connection]:
    """A missing ledger during service must never automatically open a new spending allowance."""
    mode = "rwc" if create else "rw"
    try:
        with closing(
            sqlite3.connect(
                f"{path.resolve().as_uri()}?mode={mode}",
                uri=True,
                timeout=1,
                isolation_level=None,
            )
        ) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
    except sqlite3.Error:
        raise GuardError(
            "Accounting is unavailable. Check the local portfolio ledger."
        ) from None


def initialize(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _transaction(path, create=True) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()
        if exists:
            raise GuardError("The ledger already exists. Its accounting was preserved.")
        db.execute(
            "CREATE TABLE settings (name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
        )
        db.executemany(
            "INSERT INTO settings VALUES (?,?)", {**DEFAULTS, "enabled": 0}.items()
        )
        db.execute("""CREATE TABLE runs (
            id TEXT PRIMARY KEY, identity TEXT NOT NULL, started REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, tools INTEGER NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE calls (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
            amount INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER)""")
        db.execute("CREATE INDEX calls_by_run ON calls(run_id)")
        db.execute("CREATE INDEX runs_by_identity ON runs(identity)")
    path.chmod(0o600)


def _limits(db: sqlite3.Connection) -> dict[str, int]:
    values = dict(db.execute("SELECT name, value FROM settings"))
    if not DEFAULTS.keys() <= values.keys() or values.get("enabled") != 1:
        raise GuardError("Live mode is disabled. The offline demo is available.")
    return values


def _worker() -> None:
    """Fixed-host, non-redirecting HTTPS. The parent enforces a wall-clock timeout."""
    try:
        action, key, data = json.load(sys.stdin)
        route = {"count": "/v1/responses/input_tokens", "generate": "/v1/responses"}[
            action
        ]
        with closing(http.client.HTTPSConnection("api.openai.com", timeout=30)) as conn:
            conn.request(
                "POST",
                route,
                json.dumps(data).encode(),
                {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError
            raw = response.read(256_001)
            if len(raw) > 256_000:
                raise ValueError
            sys.stdout.buffer.write(raw)
    except Exception:
        # Provider text, headers, keys and prompts never enter exception or access logs.
        raise SystemExit(1) from None


def _request(
    action: str, key: str, data: dict[str, Any], timeout: float
) -> dict[str, Any]:
    try:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-I", str(Path(__file__).resolve()), "_worker"],
            input=json.dumps([action, key, data]).encode(),
            capture_output=True,
            check=True,
            timeout=timeout,
        )
        parsed = json.loads(result.stdout)
        if not isinstance(parsed, dict):
            raise ValueError
        return parsed
    except (OSError, ValueError, subprocess.SubprocessError):
        raise GuardError(
            "The provider did not return a usable response. Try the offline demo."
        ) from None


class Guard:
    def __init__(
        self,
        path: Path,
        key_loader: Callable[[], str] = _key,
        request: Callable[[str, str, dict[str, Any], float], dict[str, Any]] = _request,
    ):
        self.path = path
        self.key_loader = key_loader
        self.request = request

    @classmethod
    def from_env(cls) -> Guard:
        return cls(_state())

    @contextmanager
    def investigation(self, identity: str) -> Iterator[Investigation]:
        # A trusted server sets identity, never a request field. Only its digest is retained.
        digest = hashlib.sha256(identity.encode()).hexdigest()
        run_id = secrets.token_hex(16)
        now = time.time()
        with _transaction(self.path) as db:
            limits = _limits(db)
            active = db.execute("SELECT count(*) FROM runs WHERE active=1").fetchone()[
                0
            ]
            recent = db.execute(
                "SELECT count(*) FROM runs WHERE started>=?", (now - 60,)
            ).fetchone()[0]
            if active >= limits["concurrency"] or recent >= limits["starts_per_minute"]:
                raise GuardError("Portfolio rate or concurrency limit reached.")
            db.execute(
                "INSERT INTO runs(id,identity,started) VALUES(?,?,?)",
                (run_id, digest, now),
            )
        run = Investigation(self, run_id, digest, time.monotonic() + limits["seconds"])
        try:
            yield run
            run.check()
        finally:
            with _transaction(self.path) as db:
                db.execute("UPDATE runs SET active=0 WHERE id=?", (run_id,))


class Investigation:
    def __init__(self, guard: Guard, run_id: str, identity: str, deadline: float):
        self.guard, self.run_id, self.identity, self.deadline = (
            guard,
            run_id,
            identity,
            deadline,
        )

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise GuardError("Investigation time limit reached.")
        return remaining

    def _active(self, db: sqlite3.Connection) -> dict[str, int]:
        self.remaining()
        limits = _limits(db)
        if not db.execute(
            "SELECT 1 FROM runs WHERE id=? AND active=1", (self.run_id,)
        ).fetchone():
            raise GuardError("Investigation is closed.")
        return limits

    def check(self) -> None:
        with _transaction(self.guard.path) as db:
            self._active(db)

    def tool(self, operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        with _transaction(self.guard.path) as db:
            limits = self._active(db)
            changed = db.execute(
                "UPDATE runs SET tools=tools+1 WHERE id=? AND tools<?",
                (self.run_id, limits["tool_calls"]),
            )
            if changed.rowcount != 1:
                raise GuardError("Investigation tool limit reached.")
        result = operation(*args, **kwargs)
        self.check()
        return result

    def _reserve(self, call_id: str) -> tuple[int, int]:
        with _transaction(self.guard.path) as db:
            limits = self._active(db)
            max_in, max_out = limits["input_tokens"], limits["output_tokens"]
            reserve = max_in * INPUT_RATE + max_out * OUTPUT_RATE
            calls, run_spend, user_spend, total = db.execute(
                """SELECT count(CASE WHEN run_id=? THEN 1 END),
                   coalesce(sum(CASE WHEN run_id=? THEN amount END),0),
                   coalesce(sum(CASE WHEN identity=? THEN amount END),0),
                   coalesce(sum(amount),0) FROM calls JOIN runs ON runs.id=calls.run_id""",
                (self.run_id, self.run_id, self.identity),
            ).fetchone()
            if calls >= limits["model_calls"]:
                raise GuardError("Investigation model-call limit reached.")
            for spent, scope in [
                (run_spend, "run_cents"),
                (user_spend, "user_cents"),
                (total, "global_cents"),
            ]:
                if spent + reserve > limits[scope] * 10_000_000:
                    raise GuardError(
                        "Insufficient portfolio budget for a full reservation."
                    )
            db.execute(
                "INSERT INTO calls(id,run_id,amount) VALUES(?,?,?)",
                (call_id, self.run_id, reserve),
            )
        return max_in, max_out

    def model(self, instructions: str, prompt: str) -> str:
        self.check()
        key = self.guard.key_loader()
        call_id = secrets.token_hex(16)
        max_in, max_out = self._reserve(call_id)
        fields = {"model": MODEL, "instructions": instructions, "input": prompt}
        # Count the exact same immutable messages. No previous responses, files or remote tools.
        if len(instructions.encode()) + len(prompt.encode()) > 128_000:
            raise GuardError("Context is too large.")
        counted = self.guard.request("count", key, fields, self.remaining()).get(
            "input_tokens"
        )
        if type(counted) is not int or not (0 < counted <= max_in):
            raise GuardError(
                "Input-token limit exceeded or the exact count is unavailable."
            )
        self.check()
        reply = self.guard.request(
            "generate",
            key,
            {
                **fields,
                "max_output_tokens": max_out,
                "service_tier": "default",
                "store": False,
                "truncation": "disabled",
                "reasoning": {"effort": "none"},
            },
            self.remaining(),
        )
        usage = reply.get("usage")
        if not isinstance(usage, dict):
            raise GuardError("Missing final usage. The full reservation was retained.")
        inp, out = usage.get("input_tokens"), usage.get("output_tokens")
        if not (type(inp) is int and type(out) is int and inp >= 0 and out >= 0):
            raise GuardError("Unrecognized usage. The full reservation was retained.")
        priced = reply.get("model") == MODEL and reply.get("service_tier") == "default"
        anomaly = not priced or inp != counted or inp > max_in or out > max_out
        amount = inp * INPUT_RATE + out * OUTPUT_RATE
        if anomaly:
            amount = max(amount, max_in * INPUT_RATE + max_out * OUTPUT_RATE)
        with _transaction(self.guard.path) as db:
            db.execute(
                "UPDATE calls SET amount=?,input_tokens=?,output_tokens=? WHERE id=?",
                (amount, inp, out, call_id),
            )
            if anomaly:
                db.execute("UPDATE settings SET value=0 WHERE name='enabled'")
        if anomaly:
            raise GuardError(
                "Provider accounting differed from the verified policy. Live mode disabled."
            )
        self.check()
        if reply.get("status") != "completed":
            raise GuardError("Incomplete model response.")
        try:
            answer = "".join(
                part["text"]
                for item in reply["output"]
                if item["type"] == "message"
                for part in item["content"]
                if part["type"] == "output_text"
            )
            if not answer.strip():
                raise ValueError
            return answer
        except (TypeError, KeyError, ValueError):
            raise GuardError("The model did not return a text response.") from None


def _admin() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "enable", "disable", "status", "recover"):
        subs.add_parser(name)
    configure = subs.add_parser("set")
    configure.add_argument("name", choices=DEFAULTS)
    configure.add_argument("value", type=int)
    args = parser.parse_args()
    path = _state()
    if args.command == "init":
        initialize(path)
        print("Created disabled ledger at", path)
        return
    with _transaction(path) as db:
        if args.command == "status":
            print(
                json.dumps(
                    dict(db.execute("SELECT name,value FROM settings")), indent=2
                )
            )
            amount, calls = db.execute(
                "SELECT coalesce(sum(amount),0),count(*) FROM calls"
            ).fetchone()
            print(f"{calls} reservations; charged or held USD {amount / 1e9:.9f}")
        elif args.command == "set":
            if not 1 <= args.value <= 1_000_000:
                raise GuardError("Use a positive integer no greater than 1,000,000.")
            db.execute(
                "UPDATE settings SET value=? WHERE name=?", (args.value, args.name)
            )
        elif args.command == "recover":
            if db.execute("SELECT value FROM settings WHERE name='enabled'").fetchone()[
                0
            ]:
                raise GuardError(
                    "Stop all three local servers and disable live mode first."
                )
            db.execute("UPDATE runs SET active=0")
        else:
            db.execute(
                "UPDATE settings SET value=? WHERE name='enabled'",
                (int(args.command == "enable"),),
            )


if __name__ == "__main__":
    if sys.argv[1:] == ["_worker"]:
        _worker()
    else:
        try:
            _admin()
        except GuardError as exc:
            raise SystemExit(str(exc)) from None
