"""Start a detached loopback server, then open the review workspace."""

import argparse
import json
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import closing
from http.client import HTTPConnection, HTTPException
from pathlib import Path


DEFAULT_PORTS = (8765, 18765)


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


def start_server(port):
    detach = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
              if sys.platform == "win32" else {"start_new_session": True})
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("app.py")), "--port", str(port),
         "--idle-minutes", "45"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **detach,
    )
    for _ in range(60):
        if app_is_running(port):
            return True
        if child.poll() is not None:
            break
        time.sleep(.1)
    if child.poll() is None:
        # Clean up only the process this launcher started if it never became ready.
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    return False


def select_port(requested=None):
    if requested is not None and not 1 <= requested <= 65535:
        raise SystemExit("Choose a loopback port from 1 through 65535.")
    preferred = DEFAULT_PORTS if requested is None else (requested,)
    for port in preferred:
        if app_is_running(port):
            return port
    # A zero bind asks the OS for an available port when both known ports are occupied.
    # Retry if a separate process acquires that port between the probe and the child bind.
    candidates = (*preferred, 0, 0, 0) if requested is None else preferred
    for candidate in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            port = probe.getsockname()[1]
        if start_server(port):
            return port
    if requested is not None:
        raise SystemExit(f"Could not start Readmission Review Agent on 127.0.0.1:{requested}. "
                         "Omit --port to select an available port.")
    raise SystemExit("Could not start the local app. Check that Python 3.10 or later is available.")


def launch(port=None):
    port = select_port(port)
    url = f"http://127.0.0.1:{port}/"
    webbrowser.open(url)
    print("Readmission Review Agent is open at", url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help="Use only this loopback port.")
    launch(parser.parse_args().port)
