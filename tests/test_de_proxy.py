"""The de-eli proxy must survive a de-eli restart.

A FastMCP server forgets its sessions when it restarts, and the MCP Streamable HTTP
spec answers a request that carries an unknown session id with HTTP 404. The client
is then expected to initialize again. The fake server below behaves exactly that way,
so these tests need no network and no de-eli install.

    python -m unittest tests.test_de_proxy
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402  (importing does not load any backend; build() does)


class FakeMcp:
    """Streamable-HTTP MCP server that forgets every session on restart()."""

    def __init__(self) -> None:
        self.sessions = set()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                sid = self.headers.get("Mcp-Session-Id")
                method = body.get("method")
                if method == "initialize":
                    new = uuid.uuid4().hex
                    outer.sessions.add(new)
                    return self._reply({"jsonrpc": "2.0", "id": body.get("id"), "result": {
                        "protocolVersion": "2025-06-18", "capabilities": {},
                        "serverInfo": {"name": "fake-de-eli", "version": "1"}}}, sid=new)
                if sid not in outer.sessions:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Session not found")
                    return
                if method == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                if method == "tools/list":
                    return self._reply({"jsonrpc": "2.0", "id": body.get("id"), "result": {"tools": [
                        {"name": "de_echo", "description": "echo", "inputSchema": {"type": "object"}}]}})
                if method == "tools/call":
                    return self._reply({"jsonrpc": "2.0", "id": body.get("id"), "result": {
                        "structuredContent": {"echo": body["params"]["arguments"]}}})
                self.send_response(400)
                self.end_headers()

            def _reply(self, payload, sid=None):
                data = ("event: message\ndata: %s\n\n" % json.dumps(payload)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                if sid:
                    self.send_header("Mcp-Session-Id", sid)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/mcp" % self.httpd.server_address[1]
        self.stopped = False
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def restart(self) -> None:
        self.sessions.clear()

    def stop(self) -> None:
        if not self.stopped:
            self.stopped = True
            self.httpd.shutdown()
            self.httpd.server_close()


class DeProxySessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeMcp()
        self.proxy = server._DeEliProxy(self.fake.url)
        self.proxy.connect()

    def tearDown(self) -> None:
        self.fake.stop()

    def test_call_works_on_a_fresh_session(self) -> None:
        self.assertEqual(self.proxy.call("de_echo", {"q": 1}), {"echo": {"q": 1}})

    def test_call_survives_a_de_eli_restart(self) -> None:
        self.assertEqual(self.proxy.call("de_echo", {"q": 1}), {"echo": {"q": 1}})
        self.fake.restart()  # the old session id is now unknown: the server answers 404
        self.assertEqual(self.proxy.call("de_echo", {"q": 2}), {"echo": {"q": 2}})

    def test_concurrent_calls_after_restart_open_one_session(self) -> None:
        self.fake.restart()
        results, errors = [], []

        def worker(i: int) -> None:
            try:
                results.append(self.proxy.call("de_echo", {"i": i}))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(len(self.fake.sessions), 1)

    def test_a_dead_backend_still_raises(self) -> None:
        self.fake.stop()
        with self.assertRaises(urllib.error.URLError):
            self.proxy.call("de_echo", {"q": 3})


class StatusProbeTest(unittest.TestCase):
    """`status` must report a German backend that stopped answering after boot."""

    def setUp(self) -> None:
        self.fake = FakeMcp()
        proxy = server._DeEliProxy(self.fake.url)
        proxy.connect()
        self._saved = server._de_proxy
        server._de_proxy = proxy

    def tearDown(self) -> None:
        server._de_proxy = self._saved
        self.fake.stop()

    def test_status_reports_live_backend_and_heals_a_lost_session(self) -> None:
        self.fake.restart()
        live = server._t_status({})["de_live"]
        self.assertTrue(live["reachable"])
        self.assertEqual(live["tools"], 1)
        self.assertEqual(live["session_renewals"], 1)

    def test_status_reports_dead_backend(self) -> None:
        self.fake.stop()
        out = server._t_status({})
        self.assertFalse(out["de_live"]["reachable"])
        self.assertIn("German backend", out["warning"])


if __name__ == "__main__":
    unittest.main()
