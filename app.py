"""Local HTTP server for the Readmission Review Agent demo."""

import argparse
import json
import mimetypes
import sqlite3
import time
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from threading import Thread
from uuid import uuid4

from investigation import CASE_IDS, FIXTURES, ROOT, CaseData, ToolSelectionError, investigate
from live import make_chooser
from portfolio_guard import Guard, GuardError

REPLAYS = ROOT / "data" / "replays.json"
STATIC = ROOT / "docs"
PUBLIC_ASSETS = {"app.css", "app.js", "recordings.js"}


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
    def log_message(self, *_):
        # The default HTTP server logs URLs, which may contain user-supplied data.
        pass

    def valid_host(self):
        port = self.server.server_port
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        supplied = self.headers.get_all("Host", [])
        return len(supplied) == 1 and supplied[0] in hosts

    def local_request(self, *, check_origin=False):
        self.server.last_request = time.monotonic()
        if not self.valid_host():
            self.reply_json(403, {"message": "Open the local loopback URL."})
            return False
        origins = self.headers.get_all("Origin", [])
        if check_origin and origins and origins != ["http://" + self.headers["Host"]]:
            self.reply_json(403, {"message": "The local page must start this request."})
            return False
        return True

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
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/cases":
            try:
                cards = [case_card(case_id, self.fixture_path()) for case_id in CASE_IDS]
            except (OSError, sqlite3.Error, ValueError):
                self.reply_json(500, {"message": "Could not load the synthetic fixture data."})
                return
            self.reply_json(200, {"app": "readmission-review-agent", "cases": cards,
                                  "live_available": self.server.live_session is not None})
            return

        if path in ("/", "/index.html", "/static/index.html"):
            page = (STATIC / "index.html").read_bytes()
            page = page.replace(b'data-service="pages"', b'data-service="local"')
            page = page.replace(b'./assets/', b'/assets/')
            self.reply(200, page, "text/html; charset=utf-8")
            return
        # Keep the original /static asset paths compatible with local bookmarks.
        prefix = next((prefix for prefix in ("/assets/", "/static/") if path.startswith(prefix)), None)
        name = path[len(prefix):] if prefix else None
        if name in PUBLIC_ASSETS:
            target = STATIC / "assets" / name
            self.reply(200, target.read_bytes(), mimetypes.guess_type(name)[0])
        else:
            self.reply_json(404, {"message": "Page not found."})

    def do_POST(self):
        if not self.local_request(check_origin=True):
            return
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
        if mode == "live" and self.server.live_session is None:
            self.reply_json(409, {"message": "Enable the local spending controls and configure the provider key to use Live agent."})
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
                session = self.server.live_session() if mode == "live" else nullcontext()
                complete = None
                with session as run:
                    if mode == "replay":
                        replay = load_replay()
                        ran_at = replay["captured_at"]
                        events = replay["cases"][case_id]
                    else:
                        ran_at = datetime.now(timezone.utc).isoformat()
                        data = CaseData(case_id, self.fixture_path(), deadline=run.deadline if run else None)
                        events = (investigate(data, make_chooser(run), dispatch=run.tool)
                                  if mode == "live" else investigate(data))
                    for raw_event in events:
                        event = add_provenance(raw_event, mode, ran_at)
                        if event["type"] == "complete":
                            complete = event
                            continue
                        if run:
                            self.connection.settimeout(run.remaining())
                        send_event(event)
                        if mode == "replay" and event["type"] == "step":
                            time.sleep(0.4)
                if complete:
                    complete["result"]["run_id"] = self.server.reviews.completed()
                    send_event(complete)
            except (ToolSelectionError, GuardError) as error:
                send_event({"type": "error", "message": str(error)})
            except (OSError, sqlite3.Error, ValueError, KeyError):
                send_event({"type": "error", "message": "Could not finish the investigation. Retry the run."})
        except (BrokenPipeError, ConnectionResetError):
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--idle-minutes", type=int, default=0,
                        help="Automatically stop the local server after this many inactive minutes.")
    args = parser.parse_args()
    if not FIXTURES.is_file() or not REPLAYS.is_file():
        parser.error("Missing fixtures or replay traces. See README.md for rebuild commands.")

    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.live_session = lambda: Guard.from_env().investigation("local-owner")
        server.last_request = time.monotonic()
        if args.idle_minutes > 0:
            def stop_when_idle():
                while time.monotonic() - server.last_request < 60 * args.idle_minutes:
                    time.sleep(5)
                server.shutdown()
            Thread(target=stop_when_idle, daemon=True).start()
        server.reviews = ReviewStore(ROOT / "data" / "review_state.sqlite3")
        print(f"Readmission Review Agent · http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
