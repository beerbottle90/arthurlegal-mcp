#!/usr/bin/env python3
"""az-relay -- a private CONNECT relay that lets the hosted endpoint reach e-qanun.az.

From Fly's Amsterdam region api.e-qanun.az does not answer at all (10 s timeouts,
2026-10-09); from Frankfurt it answers in half a second, from London in 0.7 s. The
main app stays in Amsterdam, where everything else works, and sends only its
e-qanun traffic through this relay in Frankfurt.

What it will and will not do:

- Tunnel TLS to two hosts on port 443 (api.e-qanun.az, e-qanun.az) and refuse
  everything else. It is not an open proxy.
- Listen on Fly's private network only: the app has no public IP and no service,
  so only apps of the same organization reach it, as arthurlegal-az-relay.internal.
- See host names, never content: TLS runs end to end between the main app and
  e-qanun.az.
- Hold the one global load budget: at most 30 tunnels a minute, whichever
  machine of the main app asks (RELAY_DAKIKA_AZAMI can lower it, not raise it).

    python relay.py                  # listens on [::]:8080 (RELAY_PORT)

Standard library only.
"""

from __future__ import annotations

import collections
import os
import select
import socket
import socketserver
import sys
import threading
import time
from typing import Deque, Set, Tuple

ALLOWED: Set[Tuple[str, int]] = {("api.e-qanun.az", 443), ("e-qanun.az", 443)}
PER_MINUTE = max(1, min(30, int(os.environ.get("RELAY_DAKIKA_AZAMI", "30") or 30)))
CONNECT_TIMEOUT_S = 10.0
IDLE_TIMEOUT_S = 60.0


class Budget:
    """At most `per_minute` tunnels in any 60-second window."""

    def __init__(self, per_minute: int, clock=time.monotonic) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._opened: Deque[float] = collections.deque()
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            now = self._clock()
            while self._opened and now - self._opened[0] >= 60.0:
                self._opened.popleft()
            if len(self._opened) >= self.per_minute:
                return False
            self._opened.append(now)
            return True


BUDGET = Budget(PER_MINUTE)


def _log(message: str) -> None:
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), message))


def pump(client: socket.socket, upstream: socket.socket, idle: float = IDLE_TIMEOUT_S) -> None:
    """Copy bytes both ways until either side closes or the tunnel sits idle."""
    sockets = [client, upstream]
    while True:
        readable, _, _ = select.select(sockets, [], [], idle)
        if not readable:
            return
        for sock in readable:
            data = sock.recv(65536)
            if not data:
                return
            (upstream if sock is client else client).sendall(data)


class Handler(socketserver.StreamRequestHandler):
    allowed: Set[Tuple[str, int]] = ALLOWED
    budget: Budget = BUDGET

    def handle(self) -> None:
        request = self.rfile.readline(4096).decode("latin-1").strip()
        while True:  # the headers of a CONNECT carry nothing the relay needs
            line = self.rfile.readline(4096)
            if line in (b"\r\n", b"\n", b""):
                break
        parts = request.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            return self._reply(405, "only CONNECT is relayed")
        host, _, port = parts[1].rpartition(":")
        try:
            target = (host.lower(), int(port))
        except ValueError:
            return self._reply(400, "bad target")
        if target not in self.allowed:
            _log("refused %s:%s" % target)
            return self._reply(403, "host not allowed")
        if not self.budget.take():
            _log("over budget %s:%s" % target)
            return self._reply(429, "relay budget is %d tunnels a minute" % self.budget.per_minute)
        started = time.monotonic()
        try:
            upstream = socket.create_connection(target, timeout=CONNECT_TIMEOUT_S)
        except OSError as exc:
            _log("upstream unreachable %s:%s (%s)" % (target[0], target[1], exc))
            return self._reply(502, "upstream unreachable")
        try:
            self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self.wfile.flush()
            pump(self.connection, upstream)
        finally:
            upstream.close()
        _log("tunnel %s:%s %.2f s" % (target[0], target[1], time.monotonic() - started))

    def _reply(self, code: int, text: str) -> None:
        body = text.encode("utf-8")
        self.wfile.write(b"HTTP/1.1 %d %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n"
                         b"Connection: close\r\n\r\n%s" % (code, text.encode("latin-1", "replace"),
                                                           len(body), body))


class Server(socketserver.ThreadingTCPServer):
    """Dual-stack listener: Fly's private network is IPv6, local tests use IPv4."""

    address_family = socket.AF_INET6
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main() -> None:
    port = int(os.environ.get("RELAY_PORT", "8080"))
    with Server(("::", port), Handler) as server:
        _log("az-relay on [::]:%d -> %s, at most %d tunnels a minute"
             % (port, ", ".join("%s:%d" % t for t in sorted(ALLOWED)), PER_MINUTE))
        server.serve_forever()


if __name__ == "__main__":
    main()
