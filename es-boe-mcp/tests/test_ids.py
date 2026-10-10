"""Consolidated-act ids from regional gazettes (2026-10-10).

245 of the 12,376 consolidated acts keep the id of the regional gazette that first
published them, with a lower-case series letter: DOGC-f-1997-90001. The client
upper-cased every id and accepted only BOE-..., so get_act and get_act_text could
not open any of them, although search found them; the API rejects DOGC-F-....

    python -m unittest discover -s tests          (from es-boe-mcp/)
"""

from __future__ import annotations

import unittest
from unittest import mock

import boe
import es_fakes


class ConsolidatedIdTests(unittest.TestCase):
    def test_the_id_is_spelled_as_the_api_expects(self):
        self.assertEqual(boe.consolidated_id("boe-a-2010-10544"), "BOE-A-2010-10544")
        self.assertEqual(boe.consolidated_id("DOGC-f-1997-90001"), "DOGC-f-1997-90001")
        self.assertEqual(boe.consolidated_id("dogc-F-1997-90001"), "DOGC-f-1997-90001")
        self.assertEqual(boe.consolidated_id(" BOJA-b-2012-90003 "), "BOJA-b-2012-90003")
        for bad in ("", "X-1", "../etc", "BOE-A-2010-10544/texto"):
            self.assertEqual(boe.consolidated_id(bad), "", bad)

    def test_a_regional_act_is_requested_with_its_lower_case_letter(self):
        fake = es_fakes.FakeBoe()
        with mock.patch.object(boe, "_get", fake):
            with self.assertRaises(boe.BoeError):        # not among the fixtures: 404
                boe.BoeClient()._part("DOGC-F-1997-90001", "texto")
        self.assertTrue(fake.calls[-1].endswith("/id/DOGC-f-1997-90001/texto"), fake.calls[-1])

    def test_a_malformed_id_is_refused_before_any_request(self):
        fake = es_fakes.FakeBoe()
        with mock.patch.object(boe, "_get", fake):
            with self.assertRaises(boe.BoeError) as ctx:
                boe.BoeClient()._part("../etc", "texto")
        self.assertIn("Malformed", str(ctx.exception))
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
