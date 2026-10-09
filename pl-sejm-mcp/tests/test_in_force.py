"""in_force follows the Sejm API's own flag, which is now text (defect #1).

Captured 2026-10-09: GET /eli/acts/DU/1964/93 (Kodeks cywilny) answers
``inForce: "IN_FORCE"`` with ``status: "akt posiada tekst jednolity"``. The old
check, ``inForce is True or status == "obowiązujący"``, read that as NOT in
force — for the Civil Code and for every act that has a consolidated text.

    python -m unittest discover -s tests          (from pl-sejm-mcp/)
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import pl_fakes
import retrieval
import sejm


class NormInForceTests(unittest.TestCase):
    def test_civil_code_is_in_force(self):
        act = sejm._norm(pl_fakes.fixture("act_DU_1964_93.json"))
        self.assertEqual(act["status"], "akt posiada tekst jednolity")
        self.assertIs(act["in_force"], True)
        self.assertNotIn("in_force_warning", act)

    def test_amending_act_covered_by_consolidated_text_is_in_force(self):
        act = sejm._norm(pl_fakes.fixture("act_DU_2025_1508.json"))
        self.assertEqual(act["status"], "akt objęty tekstem jednolitym")
        self.assertIs(act["in_force"], True)

    def test_not_in_force_text_value(self):
        act = sejm._norm({"inForce": "NOT_IN_FORCE", "status": "wygaśnięcie aktu"})
        self.assertIs(act["in_force"], False)

    def test_unknown_values_are_none_with_a_warning(self):
        for raw in ("UNKNOWN", "SOMETHING_NEW"):
            act = sejm._norm({"inForce": raw, "status": "obowiązujący"})
            self.assertIsNone(act["in_force"], raw)
            self.assertIn(raw, act["in_force_warning"])

    def test_legacy_boolean_flag_is_still_read(self):
        self.assertIs(sejm._norm({"inForce": True, "status": "x"})["in_force"], True)
        self.assertIs(sejm._norm({"inForce": False, "status": "uchylony"})["in_force"], False)

    def test_status_label_is_the_fallback_when_the_flag_is_missing(self):
        self.assertIs(sejm._norm({"status": "obowiązujący"})["in_force"], True)
        self.assertIs(sejm._norm({"status": "uchylony"})["in_force"], False)
        act = sejm._norm({"status": "bez statusu"})
        self.assertIsNone(act["in_force"])
        self.assertIn("in_force_warning", act)

    def test_citation_carries_the_in_force_flag(self):
        act = sejm._norm(pl_fakes.fixture("act_DU_1964_93.json"))
        self.assertIn("IN_FORCE", act["citation"])
        self.assertIn("akt posiada tekst jednolity", act["citation"])


class InForceOnlyTests(unittest.TestCase):
    def setUp(self):
        self.fake = pl_fakes.FakeSejm()
        patches = [mock.patch.object(sejm, "_get", self.fake),
                   mock.patch.object(retrieval, "_probe", lambda force=False: False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_live_filter_returns_only_in_force_acts(self):
        out = sejm.SejmClient().search(title="Kodeks cywilny", in_force_only=True, limit=100)
        self.assertEqual(self.fake.calls[-1][1].get("inForce"), 1)
        self.assertTrue(out["results"])
        flags = {r["address"]: r["in_force"] for r in out["results"]}
        self.assertIs(flags["WDU19640160093"], True)          # the Code itself
        self.assertTrue(all(v is True for v in flags.values()), flags)

    def test_indexed_filter_keeps_acts_with_a_consolidated_text(self):
        tmp = tempfile.mkdtemp()
        server = pl_fakes.load_module("pl_server_inforce", "server.py",
                                      os.path.join(tmp, "index.db"))
        idx = server._index
        # A row exactly as the pre-fix crawl wrote it: label kept, in_force wrong.
        idx.upsert({"ref": "WDU19640160093", "title": "Ustawa z dnia 23 kwietnia 1964 r. - Kodeks cywilny.",
                    "body": "Ustawa", "status": "akt posiada tekst jednolity",
                    "meta": {"in_force": False}})
        idx.upsert({"ref": "WDU19690130094", "title": "Ustawa z dnia 19 kwietnia 1969 r. Kodeks karny.",
                    "body": "Ustawa", "status": "uchylony", "meta": {"in_force": False}})
        idx.upsert({"ref": "WDU19740240141", "title": "Ustawa z dnia 26 czerwca 1974 r. Kodeks pracy.",
                    "body": "Ustawa", "status": "obowiązujący", "meta": {"in_force": True}})
        # A row from a crawl that recorded the API flag: the flag wins over the label.
        idx.upsert({"ref": "WDU20250000001", "title": "Kodeks testowy bez statusu",
                    "body": "Ustawa", "status": "bez statusu",
                    "meta": {"in_force": True, "in_force_api": "IN_FORCE"}})
        idx.db.commit()
        idx.reindex_fts()

        out = server._t_search_indexed({"query": "kodeks", "mode": "lexical",
                                        "in_force_only": True})
        refs = [r["ref"] for r in out["results"]]
        self.assertIn("WDU19640160093", refs)
        self.assertIn("WDU19740240141", refs)
        self.assertIn("WDU20250000001", refs)
        self.assertNotIn("WDU19690130094", refs)
        self.assertTrue(all(r["in_force"] is True for r in out["results"]))

    def test_crawl_records_the_api_flag(self):
        tmp = tempfile.mkdtemp()
        crawl = pl_fakes.load_module("pl_crawl_inforce", "crawl.py")
        idx = retrieval.Index(os.path.join(tmp, "crawl.db"))
        with mock.patch.object(crawl.time, "sleep", lambda s: None):
            crawl.crawl(idx, "DU", 1964, 1964)
        doc = idx.get("WDU19640160093")
        self.assertIs(doc["meta"]["in_force"], True)
        self.assertEqual(doc["meta"]["in_force_api"], "IN_FORCE")


if __name__ == "__main__":
    unittest.main()
