"""search_by_title puts the act that bears the name first (defect #2).

The Sejm returns title matches newest first. "Kodeks cywilny" matches 61 acts and
the Code itself (DU/1964/93) is 59th, behind every amending act and every
consolidated-text notice. The old tool asked for one page of 20, so the Code
was never even a candidate, and the BM25 rerank scored all 20 at 2e-06 because
every one of them contains both words.

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

CIVIL_CODE = "DU/1964/93"


def _fake_embed(texts, timeout=60, input_type=None):
    """A model that prefers amending acts: the worst case for the Code."""
    out = []
    for t in texts:
        if input_type == "query" or "o zmianie" in t:
            out.append([1.0, 0.0])
        else:
            out.append([0.0, 1.0])
    return out


class TitleSearchTests(unittest.TestCase):
    def setUp(self):
        self.fake = pl_fakes.FakeSejm()
        self.semantic = False
        patches = [mock.patch.object(sejm, "_get", self.fake),
                   mock.patch.object(retrieval, "_probe", lambda force=False: self.semantic),
                   mock.patch.object(retrieval, "_embed", _fake_embed)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        tmp = tempfile.mkdtemp()
        self.server = pl_fakes.load_module("pl_server_title", "server.py",
                                           os.path.join(tmp, "index.db"))

    def search(self, **args):
        args.setdefault("title", "Kodeks cywilny")
        return self.server._t_search_title(args)

    def test_civil_code_comes_first(self):
        out = self.search()
        self.assertEqual(out["results"][0]["eli"], CIVIL_CODE)
        self.assertIs(out["results"][0]["in_force"], True)

    def test_civil_code_first_in_a_short_page(self):
        out = self.search(limit=5)
        self.assertEqual(len(out["results"]), 5)
        self.assertEqual(out["results"][0]["eli"], CIVIL_CODE)

    def test_semantic_preference_for_amendments_does_not_bury_the_code(self):
        self.semantic = True
        out = self.search(limit=5)
        self.assertEqual(out["results"][0]["eli"], CIVIL_CODE)
        self.assertEqual(out["ranking"]["method"], "semantic")

    def test_amending_acts_and_notices_rank_below_the_code(self):
        out = self.search(limit=100)
        labels = [r["title_match"] for r in out["results"]]
        self.assertEqual(labels[0], "exact title")
        first_amendment = next(i for i, r in enumerate(out["results"])
                               if "o zmianie" in r["title"])
        self.assertGreater(first_amendment, 0)
        self.assertEqual(out["results"][first_amendment]["title_match"],
                         "amending act or notice")

    def test_offset_pages_through_the_ranked_list(self):
        full = [r["eli"] for r in self.search(limit=20)["results"]]
        page2 = [r["eli"] for r in self.search(limit=5, offset=5)["results"]]
        self.assertEqual(page2, full[5:10])

    def test_reports_total_and_how_it_ranked(self):
        out = self.search(limit=5)
        self.assertEqual(out["count"], 61)
        self.assertEqual(out["returned"], 5)
        self.assertEqual(out["ranking"]["ranked"], 61)
        self.assertNotIn("_rerank_score", out["results"][0])

    def test_listing_without_title_keeps_upstream_paging(self):
        self.server._t_search_title({"title": "", "year": 1964, "limit": 7, "offset": 3})
        path, params = self.fake.calls[-1]
        self.assertEqual(path, "acts/search")
        self.assertEqual((params["limit"], params["offset"]), (7, 3))


class TitleTierTests(unittest.TestCase):
    def test_tiers(self):
        tier = sejm.title_match_tier
        q = "Kodeks karny"
        self.assertEqual(tier(q, "Ustawa z dnia 6 czerwca 1997 r. - Kodeks karny."), 0)
        self.assertEqual(tier(q, "Ustawa z dnia 19 kwietnia 1969 r. Kodeks karny."), 0)
        self.assertEqual(tier(q, "Ustawa z dnia 10 września 1999 r. - Kodeks karny skarbowy."), 1)
        self.assertEqual(tier(q, "Ustawa z dnia 6 czerwca 1997 r. - Przepisy wprowadzające Kodeks karny."), 2)
        self.assertEqual(tier(q, "Ustawa z dnia 7 lipca 2022 r. o zmianie ustawy - Kodeks karny oraz niektórych innych ustaw"), 3)
        self.assertEqual(tier(q, "Obwieszczenie Marszałka Sejmu Rzeczypospolitej Polskiej z dnia 9 lutego 2024 r. w sprawie ogłoszenia jednolitego tekstu ustawy - Kodeks karny"), 3)
        self.assertEqual(tier(q, "Ustawa z dnia 26 czerwca 1974 r. Kodeks pracy."), 4)
        self.assertEqual(tier("podatku dochodowym",
                              "Ustawa z dnia 26 lipca 1991 r. o podatku dochodowym od osób fizycznych."), 1)
        self.assertEqual(tier("o podatku dochodowym od osob fizycznych",
                              "Ustawa z dnia 26 lipca 1991 r. o podatku dochodowym od osób fizycznych."), 0)


if __name__ == "__main__":
    unittest.main()
