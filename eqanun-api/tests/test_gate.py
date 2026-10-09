"""The gate in front of e-qanun.az -- offline, on a fake clock.

The condition of access: never more than 30 calls a minute for all machines
together, and no load the server would notice.

    python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eqanun import EqanunClient, EqanunError  # noqa: E402
from eqanun._gate import ByteCache, Gate, GateRefused, machine_count  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds

    def wall(self):
        return 1_800_000_000.0


def gate(**kw):
    c = FakeClock()
    return Gate(clock=c.clock, sleep=c.sleep, wall=c.wall, **kw), c


def pass_through(g, n):
    for _ in range(n):
        with g:
            pass


class GateTests(unittest.TestCase):
    def test_thirty_a_minute_shared_by_two_machines_is_one_call_every_four_seconds(self):
        g, _ = gate(per_minute=30, machines=2)
        self.assertEqual(g.interval, 4.0)

    def test_calls_are_spaced(self):
        g, c = gate(per_minute=30, machines=2)
        pass_through(g, 3)
        self.assertEqual(c.slept, [4.0, 4.0])

    def test_a_full_queue_refuses_without_sending(self):
        g, c = gate(per_minute=30, machines=2, queue_s=5)
        c.sleep = lambda s: None          # callers arrive together; nobody's wait has passed
        g._sleep = c.sleep
        pass_through(g, 2)                # waits 0 s and 4 s
        with self.assertRaises(GateRefused) as ctx:
            pass_through(g, 1)            # would wait 8 s > 5 s
        self.assertIn("queue is full", str(ctx.exception))

    def test_three_failures_in_a_row_pause_the_gate(self):
        g, c = gate()
        for _ in range(3):
            g.failed()
        with self.assertRaises(GateRefused) as ctx:
            pass_through(g, 1)
        self.assertIn("3 failures in a row", str(ctx.exception))
        c.now += 61
        pass_through(g, 1)                # open again

    def test_a_success_resets_the_failure_count(self):
        g, _ = gate()
        g.failed(); g.failed(); g.ok(); g.failed()
        pass_through(g, 1)

    def test_throttling_honours_retry_after_and_backs_off(self):
        g, c = gate()
        g.throttled(retry_after=120)
        self.assertGreaterEqual(g.state()["paused_for_s"], 120)
        c.now += 121
        g.throttled()                     # second time: the pause has doubled
        self.assertGreaterEqual(g.state()["paused_for_s"], 120)

    def test_refusal_stops_for_an_hour(self):
        g, _ = gate()
        g.refused()
        self.assertGreaterEqual(g.state()["paused_for_s"], 3600)

    def test_daily_cap_is_split_between_machines(self):
        g, _ = gate(per_minute=6000, machines=2, daily=4)
        pass_through(g, 2)
        with self.assertRaises(GateRefused) as ctx:
            pass_through(g, 1)
        self.assertIn("daily budget", str(ctx.exception))

    def test_machine_count_follows_the_tkgm_setting_on_fly(self):
        saved = {k: os.environ.pop(k, None) for k in ("EQANUN_MAKINE_SAYISI", "TKGM_MAKINE_SAYISI", "FLY_APP_NAME")}
        try:
            self.assertEqual(machine_count(), 1)
            os.environ["FLY_APP_NAME"] = "arthurlegal-mcp"
            self.assertEqual(machine_count(), 2)
            os.environ["TKGM_MAKINE_SAYISI"] = "3"
            self.assertEqual(machine_count(), 3)
            os.environ["EQANUN_MAKINE_SAYISI"] = "4"
            self.assertEqual(machine_count(), 4)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


class CacheTests(unittest.TestCase):
    def test_lru_by_bytes_and_age(self):
        c = FakeClock()
        cache = ByteCache(max_bytes=10, ttl_s=60, clock=c.clock)
        cache.put("a", b"12345")
        cache.put("b", b"12345")
        cache.put("c", b"1")              # over 10 bytes: the oldest goes
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), b"12345")
        c.now += 61
        self.assertIsNone(cache.get("b"))


class ClientThroughGateTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.answer = None
        saved = urllib.request.urlopen
        urllib.request.urlopen = self.fake_urlopen
        self.addCleanup(setattr, urllib.request, "urlopen", saved)

    def fake_urlopen(self, req, timeout=None):
        self.calls.append(req.full_url)
        if isinstance(self.answer, Exception):
            raise self.answer

        class Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return b'{"data": []}'
        return Resp()

    def client(self, g, cache=True):
        return EqanunClient(timeout=5, retries=1, retry_backoff=0, gate=g,
                            cache=ByteCache(10_000, 3600) if cache else False)

    def test_a_repeated_question_is_answered_from_the_cache(self):
        g, _ = gate(per_minute=6000)
        c = self.client(g)
        c.list_sections()
        c.list_sections()
        self.assertEqual(len(self.calls), 1)

    def test_the_status_probe_bypasses_the_cache(self):
        g, _ = gate(per_minute=6000)
        c = self.client(g, cache=False)
        c.list_sections()
        c.list_sections()
        self.assertEqual(len(self.calls), 2)

    def test_a_503_pauses_and_the_next_call_is_not_sent(self):
        g, _ = gate(per_minute=6000)
        c = self.client(g, cache=False)
        self.answer = urllib.error.HTTPError("https://api.e-qanun.az/sections", 503, "busy",
                                             {"Retry-After": "90"}, None)
        with self.assertRaises(EqanunError) as ctx:
            c.list_sections()
        self.assertIn("slow down", str(ctx.exception))
        self.answer = None
        with self.assertRaises(EqanunError) as ctx:
            c.list_sections()
        self.assertIn("paused", str(ctx.exception))
        self.assertEqual(len(self.calls), 1)

    def test_timeouts_close_the_gate_after_three(self):
        g, _ = gate(per_minute=6000)
        c = self.client(g, cache=False)
        self.answer = TimeoutError("timed out")
        for _ in range(3):
            with self.assertRaises(EqanunError):
                c.list_sections()
        with self.assertRaises(EqanunError) as ctx:
            c.list_sections()
        self.assertIn("3 failures in a row", str(ctx.exception))
        self.assertEqual(len(self.calls), 3)


if __name__ == "__main__":
    unittest.main()
