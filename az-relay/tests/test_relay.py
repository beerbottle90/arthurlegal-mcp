"""az-relay -- offline, against a local echo server standing in for e-qanun.az.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import socket
import socketserver
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import relay  # noqa: E402


class Echo(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


def serve(server: socketserver.BaseServer) -> None:
    threading.Thread(target=server.serve_forever, daemon=True).start()


class RelayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo)
        self.echo.daemon_threads = True
        serve(self.echo)
        echo_target = ("127.0.0.1", self.echo.server_address[1])

        class Handler(relay.Handler):
            allowed = {echo_target, ("127.0.0.1", 1)}   # port 1: allowed but closed
            budget = relay.Budget(3)

        self.relay = relay.Server(("::", 0), Handler)
        serve(self.relay)
        self.port = self.relay.server_address[1]
        self.echo_target = echo_target

    def tearDown(self) -> None:
        for server in (self.relay, self.echo):
            server.shutdown()
            server.server_close()

    def ask(self, request: bytes) -> socket.socket:
        conn = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        conn.sendall(request)
        return conn

    def status(self, conn: socket.socket) -> bytes:
        return conn.recv(4096).split(b"\r\n", 1)[0]

    def test_an_allowed_tunnel_carries_bytes_both_ways(self) -> None:
        conn = self.ask(b"CONNECT 127.0.0.1:%d HTTP/1.1\r\nHost: x\r\n\r\n" % self.echo_target[1])
        self.assertEqual(self.status(conn), b"HTTP/1.1 200 Connection established")
        conn.sendall(b"ping")
        self.assertEqual(conn.recv(16), b"ping")
        conn.close()

    def test_any_other_host_is_refused(self) -> None:
        conn = self.ask(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        self.assertIn(b" 403 ", self.status(conn))

    def test_only_connect_is_relayed(self) -> None:
        conn = self.ask(b"GET http://api.e-qanun.az/sections HTTP/1.1\r\n\r\n")
        self.assertIn(b" 405 ", self.status(conn))

    def test_the_minute_budget_is_global(self) -> None:
        for _ in range(3):
            conn = self.ask(b"CONNECT 127.0.0.1:%d HTTP/1.1\r\n\r\n" % self.echo_target[1])
            self.assertIn(b" 200 ", self.status(conn))
            conn.close()
        conn = self.ask(b"CONNECT 127.0.0.1:%d HTTP/1.1\r\n\r\n" % self.echo_target[1])
        self.assertIn(b" 429 ", self.status(conn))

    def test_an_unreachable_upstream_is_a_502(self) -> None:
        conn = self.ask(b"CONNECT 127.0.0.1:1 HTTP/1.1\r\n\r\n")
        self.assertIn(b" 502 ", self.status(conn))

    def test_the_production_allowlist_is_two_hosts_on_443(self) -> None:
        self.assertEqual(relay.ALLOWED, {("api.e-qanun.az", 443), ("e-qanun.az", 443)})
        self.assertLessEqual(relay.PER_MINUTE, 30)


if __name__ == "__main__":
    unittest.main()
