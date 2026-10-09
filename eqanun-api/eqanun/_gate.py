"""One polite gate for every request to e-qanun.az, and a small cache in front of it.

Access rests on the site owner's verbal permission, given on one condition: never
more than 30 calls a minute and never a load the Ministry's server would notice.
So the budget is for every machine together, a request that would queue too long
is refused here and never sent, and repeated failures close the gate for a while
instead of retrying into a server that is not answering. The same shape as the
TKGM gate (tkgm-mcp/tkgm_canli.py), which has run under a similar promise.

    EQANUN_DAKIKA_AZAMI   calls a minute, all machines together     default 30
    EQANUN_MAKINE_SAYISI  machines sharing that budget              else TKGM_MAKINE_SAYISI,
                                                                    else 2 on Fly, 1 elsewhere
    EQANUN_KUYRUK_SN      longest wait in the queue, seconds        default 20
    EQANUN_GUNLUK_AZAMI   calls a day, all machines together        default 3000
    EQANUN_ONBELLEK_MB    answers kept in memory                    default 32

A cached answer costs the server nothing, so the cache sits in front of the gate.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, Optional


class GateRefused(Exception):
    """Raised instead of sending a request. The message says why, and for how long."""


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return min(max(value, lo), hi)


def machine_count() -> int:
    for name in ("EQANUN_MAKINE_SAYISI", "TKGM_MAKINE_SAYISI"):
        raw = os.environ.get(name, "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
    return 2 if os.environ.get("FLY_APP_NAME") else 1


class Gate:
    """Spacing, one request in flight, a queue limit, a daily cap and a circuit breaker."""

    def __init__(self, per_minute: float = 30.0, machines: int = 1, queue_s: float = 20.0,
                 daily: int = 3000, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 wall: Callable[[], float] = time.time) -> None:
        self.machines = max(1, int(machines))
        self.per_minute = float(per_minute)
        self.interval = 60.0 / self.per_minute * self.machines
        self.queue_s = float(queue_s)
        self.daily = max(1, int(daily) // self.machines)
        self._clock, self._sleep, self._wall = clock, sleep, wall
        self._lock = threading.Lock()
        self._single = threading.Lock()
        self._next = 0.0
        self._open_until = 0.0
        self._why = ""
        self._failures = 0
        self._pause = 60.0
        self._day = ""
        self._used = 0

    def __enter__(self) -> "Gate":
        with self._lock:
            now = self._clock()
            if now < self._open_until:
                raise GateRefused("e-qanun.az is paused for %d s (%s); no request was sent"
                                  % (math.ceil(self._open_until - now), self._why))
            today = time.strftime("%Y-%m-%d", time.gmtime(self._wall()))
            if today != self._day:
                self._day, self._used = today, 0
            if self._used >= self.daily:
                raise GateRefused("the daily budget of %d calls on this machine is used up; "
                                  "no request was sent" % self.daily)
            start = max(now, self._next)
            wait = start - now
            if wait > self.queue_s:
                raise GateRefused("e-qanun.az queue is full (the wait would be ~%d s); no request "
                                  "was sent, try again shortly" % math.ceil(wait))
            self._next = start + self.interval
            self._used += 1
        if wait > 0:
            self._sleep(wait)
        if not self._single.acquire(timeout=self.queue_s):
            raise GateRefused("another request to e-qanun.az is still running; no request was sent")
        return self

    def __exit__(self, *exc: object) -> bool:
        self._single.release()
        return False

    def ok(self) -> None:
        with self._lock:
            self._failures = 0
            self._pause = 60.0

    def throttled(self, retry_after: Optional[float] = None) -> None:
        """429/503: the server asked us to slow down. Pause, and pause longer next time."""
        with self._lock:
            self._open(max(self._pause, retry_after or 0.0), "the server asked us to slow down")
            self._pause = min(self._pause * 2.0, 900.0)

    def refused(self) -> None:
        """401/403: access was refused. Stop for an hour rather than knock again."""
        with self._lock:
            self._open(3600.0, "the server refused access")

    def failed(self) -> None:
        """A timeout, a dropped connection or a 5xx. Three in a row close the gate."""
        with self._lock:
            self._failures += 1
            if self._failures >= 3:
                self._open(self._pause, "%d failures in a row" % self._failures)
                self._pause = min(self._pause * 2.0, 900.0)
                self._failures = 0

    def _open(self, seconds: float, why: str) -> None:
        self._open_until = self._clock() + seconds
        self._why = why

    def state(self) -> Dict[str, object]:
        with self._lock:
            now = self._clock()
            paused = now < self._open_until
            return {
                "calls_per_minute_all_machines": self.per_minute,
                "machines": self.machines,
                "seconds_between_calls_here": round(self.interval, 2),
                "queue_limit_s": self.queue_s,
                "daily_cap_here": self.daily,
                "used_today_here": self._used,
                "paused": paused,
                "paused_for_s": math.ceil(self._open_until - now) if paused else 0,
                "pause_reason": self._why if paused else "",
            }


class ByteCache:
    """Least-recently-used answers by URL, bounded in bytes and in age."""

    def __init__(self, max_bytes: int, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_bytes = int(max_bytes)
        self.ttl_s = float(ttl_s)
        self._clock = clock
        self._items: "OrderedDict[str, tuple]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[bytes]:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            stored_at, body = item
            if self._clock() - stored_at > self.ttl_s:
                self._drop(key)
                return None
            self._items.move_to_end(key)
            return body

    def put(self, key: str, body: bytes) -> None:
        if len(body) > self.max_bytes:
            return
        with self._lock:
            if key in self._items:
                self._drop(key)
            self._items[key] = (self._clock(), body)
            self._bytes += len(body)
            while self._bytes > self.max_bytes and self._items:
                self._drop(next(iter(self._items)))

    def _drop(self, key: str) -> None:
        _, body = self._items.pop(key)
        self._bytes -= len(body)

    def state(self) -> Dict[str, object]:
        with self._lock:
            return {"entries": len(self._items), "megabytes": round(self._bytes / 1e6, 1),
                    "limit_megabytes": round(self.max_bytes / 1e6, 1), "ttl_hours": self.ttl_s / 3600}


GATE = Gate(
    per_minute=_env_float("EQANUN_DAKIKA_AZAMI", 30.0, 1.0, 30.0),
    machines=machine_count(),
    queue_s=_env_float("EQANUN_KUYRUK_SN", 20.0, 1.0, 120.0),
    daily=int(_env_float("EQANUN_GUNLUK_AZAMI", 3000, 1, 20000)),
)
CACHE = ByteCache(max_bytes=int(_env_float("EQANUN_ONBELLEK_MB", 32, 0, 256) * 1e6), ttl_s=6 * 3600)
