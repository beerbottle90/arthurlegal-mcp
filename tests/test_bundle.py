"""The aggregator adopts the gesetze-im-internet.de backend under the de_ prefix.

    python -m unittest tests.test_bundle
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402


class GiiBundleTest(unittest.TestCase):
    def test_de_gii_tools_are_exposed_with_the_de_prefix(self) -> None:
        entry = next(b for b in server.STDLIB_BACKENDS if b[2] == "srv_de_gii")
        before, failed = len(server._tools), len(server._failed)
        server._load_stdlib(*entry)
        self.assertEqual(len(server._failed), failed, server._failed[failed:])
        self.assertEqual([t.name for t in server._tools[before:]], ["de_norm_getir", "de_gesetz_ara"])


if __name__ == "__main__":
    unittest.main()
