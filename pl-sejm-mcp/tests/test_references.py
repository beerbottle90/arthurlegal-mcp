"""get_act's amendment graph: filled citations, bounded size (defect #3).

The act payload's ``references`` carries only an ELI id per related act
(``{"id": "DU/2026/507", "date": "2028-11-01"}``), so the old ``display_address``
was always "". The citation lives on GET /eli/acts/{p}/{y}/{pos}/references,
whose entries carry the related act's header. For the Civil Code that graph is
373 entries; the old tool returned all of them on every call.

    python -m unittest discover -s tests          (from pl-sejm-mcp/)
"""

from __future__ import annotations

import unittest
from unittest import mock

import pl_fakes
import sejm


class ReferencesTests(unittest.TestCase):
    def setUp(self):
        self.fake = pl_fakes.FakeSejm()
        p = mock.patch.object(sejm, "_get", self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.client = sejm.SejmClient()

    def test_display_address_is_filled_from_the_references_endpoint(self):
        act = self.client.get_act("DU", 1964, 93)
        entries = [e for items in act["references"].values() for e in items]
        self.assertTrue(entries)
        self.assertTrue(all(e["display_address"] for e in entries), entries[:3])
        first = act["references"]["Akty zmieniające"][0]
        self.assertEqual(first["display_address"], "Dz.U. 2026 poz. 507")
        self.assertEqual(first["id"], "DU/2026/507")
        self.assertEqual(first["date"], "2028-11-01")
        self.assertIn("Centralnej Ewidencji", first["title"])

    def test_long_relations_are_cut_and_counted(self):
        act = self.client.get_act("DU", 1964, 93)
        totals = act["references_total"]
        self.assertEqual(totals["Akty wykonawcze"], 223)
        self.assertEqual(totals["Akty zmieniające"], 113)
        self.assertEqual(sum(totals.values()), 373)
        self.assertEqual(len(act["references"]["Akty wykonawcze"]), 10)
        self.assertEqual(len(act["references"]["Przepisy wprowadzające"]), 1)
        self.assertIn("references_truncated", act)
        self.assertLessEqual(sum(len(v) for v in act["references"].values()), 50)

    def test_one_relation_can_be_paged(self):
        act = self.client.get_act("DU", 1964, 93, relation="Akty zmieniające",
                                  references_limit=50, references_offset=100)
        self.assertEqual(list(act["references"]), ["Akty zmieniające"])
        self.assertEqual(len(act["references"]["Akty zmieniające"]), 13)
        self.assertEqual(act["references_total"]["Akty zmieniające"], 113)

    def test_endpoint_failure_degrades_to_ids_and_says_so(self):
        p = mock.patch.object(sejm, "_get", pl_fakes.FakeSejm(fail_references=True))
        p.start()
        self.addCleanup(p.stop)
        act = sejm.SejmClient().get_act("DU", 1964, 93)
        first = act["references"]["Akty zmieniające"][0]
        self.assertEqual(first["id"], "DU/2026/507")
        self.assertNotIn("display_address", first)      # never an empty string
        self.assertIn("references_warning", act)
        self.assertEqual(act["references_total"]["Akty zmieniające"], 113)

    def test_text_fetch_does_not_pull_the_graph(self):
        meta = self.client._act_meta("DU", 1964, 93)
        self.assertTrue(meta["has_html"])
        self.assertNotIn("acts/DU/1964/93/references", [c[0] for c in self.fake.calls])


if __name__ == "__main__":
    unittest.main()
