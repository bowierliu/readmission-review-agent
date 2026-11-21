"""Start a detached loopback server, then open the review workspace."""

import argparse
import json
import subprocess
import sys
import time
import webbrowser
from contextlib import closing
from http.client import HTTPConnection, HTTPException
from pathlib import Path


def app_is_running(port):
    try:
        with closing(HTTPConnection("127.0.0.1", port, timeout=1)) as conn:
            conn.request("GET", "/api/cases")
            response = conn.getresponse()
            if response.status != 200:
                return False
            return json.loads(response.read(64_000)).get("app") == "readmission-review-agent"
    except (OSError, HTTPException, ValueError, AttributeError):
        return False


def launch(port):
    if not app_is_running(port):
        detach = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                  if sys.platform == "win32" else {"start_new_session": True})
        subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("app.py")), "--port", str(port),
             "--idle-minutes", "45"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **detach,
        )
        for _ in range(60):
            if app_is_running(port):
                break
            time.sleep(.1)
        else:
            raise SystemExit("Could not start the app. Check the Python version and choose a free port.")
    url = f"http://127.0.0.1:{port}/"
    webbrowser.open(url)
    print("Readmission Review Agent is open at", url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    launch(parser.parse_args().port)
