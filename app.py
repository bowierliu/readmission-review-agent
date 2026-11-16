"""Local HTTP server for the Readmission Review Agent demo."""

import argparse
import json
import mimetypes
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

from investigation import CASE_IDS, FIXTURES, ROOT, CaseData, ToolSelectionError, investigate
from live import DEFAULT_MODEL, make_chooser

REPLAYS = ROOT / "data" / "replays.json"
STATIC = ROOT / "static"


def load_replay():
    return json.loads(REPLAYS.read_text())


def read_replay(case_id):
    return load_replay()["cases"][case_id]


def case_card(case_id, path):
    data = CaseData(case_id, path)
    with data.snapshot():
        return data.card()


def add_provenance(event, mode, ran_at):
    run_fields = {"mode": mode, "ran_at": ran_at, "saved_trace": mode == "replay"}
    if event["type"] == "complete":
        return {**event, "result": {**event["result"], **run_fields}}

    check = event["step"]
    if "drilldowns" in check:
        check = {**check, "drilldowns": [{**gap, **run_fields} for gap in check["drilldowns"]]}
    provenance = {**run_fields, "source": check["source"], "row_count": check["row_count"]}
    return {**event, **provenance, "step": {**check, **provenance}}


class ReviewStore:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS completed_runs (
                    run_id TEXT PRIMARY KEY,
                    reviewed_at TEXT
                )
            """)

    def completed(self):
        run_id = uuid4().hex
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO completed_runs (run_id) VALUES (?)", (run_id,))
        return run_id

    def mark_reviewed(self, run_id):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("""
                UPDATE completed_runs SET reviewed_at = ?
                WHERE run_id = ? AND reviewed_at IS NULL
            """, (now, run_id))
            row = conn.execute("SELECT reviewed_at FROM completed_runs WHERE run_id = ?",
                               (run_id,)).fetchone()
        return {"reviewed": True, "reviewed_at": row[0]} if row else None


class Handler(BaseHTTPRequestHandler):
    def fixture_path(self):
        return getattr(self.server, "fixture_path", FIXTURES)

    def reply(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, status, value):
        self.reply(status, json.dumps(value).encode(), "application/json")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/cases":
            try:
                cards = [case_card(case_id, self.fixture_path()) for case_id in CASE_IDS]
            except (OSError, sqlite3.Error, ValueError):
                self.reply_json(500, {"message": "Could not load the synthetic fixture data."})
                return
            self.reply_json(200, {"cases": cards, "live_available": self.server.choose is not None})
            return

        # Resolve files inside the public folder only. The database, source, and env stay private.
        name = "index.html" if path == "/" else path.removeprefix("/static/")
        target = (STATIC / name).resolve()
        if (target.is_relative_to(STATIC) and target.is_file() and not name.startswith(".")
                and target.suffix.lower() not in {".py", ".sql", ".sqlite3"}):
            content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
            self.reply(200, target.read_bytes(), content_type)
        else:
            self.reply_json(404, {"message": "Page not found."})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in {"/api/investigate", "/api/review"}:
            self.reply_json(404, {"message": "Page not found."})
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 4096 or self.headers.get_content_type() != "application/json":
                raise ValueError
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self.reply_json(400, {"message": "Provide a small JSON object."})
            return

        if path == "/api/review":
            run_id = body.get("run_id")
            marked = self.server.reviews.mark_reviewed(run_id) if isinstance(run_id, str) else None
            if marked is None:
                self.reply_json(404, {"message": "This investigation run was not found."})
            else:
                self.reply_json(200, marked)
            return

        case_id, mode = body.get("case_id"), body.get("mode", "checklist")
        if (urlsplit(self.path).query or body.keys() - {"case_id", "mode"}
                or case_id not in CASE_IDS or mode not in ("checklist", "replay", "live")):
            self.reply_json(400, {"message": "Choose a valid case and investigation mode."})
            return
        if mode == "live" and self.server.choose is None:
            self.reply_json(409, {"message": "Live mode requires a server-side API key. Use Checklist (fresh) or Saved trace."})
            return
        self.stream_investigation(case_id, mode)

    def stream_investigation(self, case_id, mode):
        def send_event(event):
            self.wfile.write((json.dumps(event) + "\n").encode())
            self.wfile.flush()

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            try:
                if mode == "replay":
                    replay = load_replay()
                    ran_at = replay["captured_at"]
                    events = replay["cases"][case_id]
                else:
                    ran_at = datetime.now(timezone.utc).isoformat()
                    choose = self.server.choose if mode == "live" else None
                    events = investigate(CaseData(case_id, self.fixture_path()), choose)
                for raw_event in events:
                    event = add_provenance(raw_event, mode, ran_at)
                    if event["type"] == "complete":
                        event["result"]["run_id"] = self.server.reviews.completed()
                    send_event(event)
                    if mode == "replay" and event["type"] == "step":
                        time.sleep(0.4)
            except ToolSelectionError as error:
                send_event({"type": "error", "message": str(error)})
            except (OSError, sqlite3.Error, ValueError, KeyError):
                send_event({"type": "error", "message": "Could not finish the investigation. Retry the run."})
        except (BrokenPipeError, ConnectionResetError):
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not FIXTURES.is_file() or not REPLAYS.is_file():
        parser.error("Missing fixtures or replay traces. See README.md for rebuild commands.")

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.choose = make_chooser(api_key, model) if api_key else None
        server.reviews = ReviewStore(ROOT / "data" / "review_state.sqlite3")
        print(f"Readmission Review Agent · http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
