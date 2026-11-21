import shutil
import socket
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

import launcher


class Unrelated(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_error(404)

    def log_message(self, *_):
        pass


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.unrelated = ThreadingHTTPServer(("127.0.0.1", 0), Unrelated)
        self.addCleanup(self.unrelated.server_close)
        thread = Thread(target=self.unrelated.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.unrelated.shutdown)
        self.busy = self.unrelated.server_port

    def test_default_reuses_fallback_and_leaves_unrelated_service_running(self):
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            fallback = candidate.getsockname()[1]
        # Launch the real HTTP entry point with a private fixture/review directory.
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = Path(launcher.__file__).resolve().parent
        for name in ("app.py", "investigation.py", "signal_stats.py", "live.py", "portfolio_guard.py"):
            shutil.copyfile(source / name, root / name)
        (root / "data").mkdir()
        for name in ("fixtures.sqlite3", "replays.json"):
            shutil.copyfile(source / "data" / name, root / "data" / name)
        shutil.copytree(source / "sql", root / "sql")
        spawn = subprocess.Popen

        def start_owned_child(*args, **kwargs):
            child = spawn(*args, **kwargs)
            self.addCleanup(child.wait, 5)
            self.addCleanup(child.terminate)
            return child

        with patch.object(launcher, "__file__", str(root / "launcher.py")), \
             patch.object(launcher, "DEFAULT_PORTS", (self.busy, fallback)), \
             patch.object(launcher.subprocess, "Popen", side_effect=start_owned_child) as starts, \
             patch.object(launcher.webbrowser, "open") as browser:
            launcher.launch()
            launcher.launch()
            self.assertEqual(starts.call_count, 1)
            self.assertEqual(browser.call_count, 2)
            browser.assert_called_with(f"http://127.0.0.1:{fallback}/")
        with closing(HTTPConnection("127.0.0.1", self.busy)) as conn:
            conn.request("GET", "/api/cases")
            self.assertEqual(conn.getresponse().status, 404)

    def test_port_is_strict_when_explicit_and_os_assigns_one_if_all_defaults_busy(self):
        with patch.object(launcher, "DEFAULT_PORTS", (self.busy,)), \
             patch.object(launcher, "start_server", return_value=True) as start:
            with self.assertRaisesRegex(SystemExit, str(self.busy)):
                launcher.select_port(self.busy)
            start.assert_not_called()
            chosen = launcher.select_port()
            self.assertNotEqual(chosen, self.busy)
            self.assertGreater(chosen, 0)
            start.assert_called_once_with(chosen)
