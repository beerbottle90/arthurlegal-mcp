"""search_legislation: every result says which provision and which version it is.

Regression found live on 2026-10-09: ten results with the same title and the
same score (0.7603) and no paragraph field. Each RIS hit is one provision (§,
Art., Anl.) in one version, but the result kept only the act's title, and the
reranker read only title fields — identical for every paragraph of one act.

Fixtures are live RIS answers: ten ABGB provisions for "Schadenersatz", five
hits holding three versions of the same Art. 8, two gazette issues.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import retrieval  # noqa: E402
import ris  # noqa: E402


def _load_server():
    # Loaded under its own name: every backend has a server.py.
    spec = importlib.util.spec_from_file_location(
        "at_server_under_test", os.path.join(ROOT, "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = _load_server()

ABGB = "Allgemeines bürgerliches Gesetzbuch"


def _fixture(name: str) -> dict:
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


class LegislationFields(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch.object(retrieval, "_probe", lambda force=False: False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def search(self, fixture: str, **args):
        payload = _fixture(fixture)
        with mock.patch.object(ris, "_get", lambda url, timeout=60: payload):
            return SERVER._t_search_legislation(args)["results"]

    def test_each_result_names_its_provision(self):
        results = self.search("brkons_abgb_schadenersatz.json",
                              title=ABGB, terms="Schadenersatz", page_size=10)
        sections = [r["section"] for r in results]
        for expected in ("§ 43", "§ 874", "§ 1014", "§ 1162a"):
            self.assertIn(expected, sections)
        self.assertEqual(len(set(sections)), 10)
        p874 = next(r for r in results if r["section"] == "§ 874")
        self.assertTrue(p874["citation"].startswith(ABGB + " § 874, JGS"), p874["citation"])
        self.assertEqual(p874["doc_type"], "Paragraph")

    def test_the_act_level_entry_is_not_cited_as_paragraph_zero(self):
        results = self.search("brkons_abgb_schadenersatz.json",
                              title=ABGB, terms="Schadenersatz", page_size=10)
        act = next(r for r in results if r["doc_type"] == "Norm")
        self.assertEqual(act["section"], "")
        self.assertNotIn("§ 0", act["citation"])

    def test_ranking_reads_the_provision_not_only_the_act_title(self):
        results = self.search("brkons_abgb_schadenersatz.json",
                              title=ABGB, terms="Schadenersatz", page_size=10)
        # § 874 and § 1014 are the two whose RIS keywords name Schadenersatz.
        self.assertEqual({r["section"] for r in results[:2]}, {"§ 874", "§ 1014"})
        self.assertIn("Schadenersatz", results[0]["keywords"])

    def test_versions_of_one_article_differ_by_validity(self):
        results = self.search("brkons_versions.json", terms="Schadenersatz", page_size=10)
        art8 = [r for r in results if r["section"] == "Art. 8"]
        self.assertEqual(len(art8), 3)
        self.assertEqual(len({(r["valid_from"], r["valid_to"]) for r in art8}), 3)
        self.assertTrue(all(r["valid_to"] for r in art8))
        current = [r for r in results if r["section"] == "§ 5" and not r["valid_to"]]
        self.assertEqual(len(current), 1)

    def test_a_gazette_issue_is_cited_by_its_bgbl_number(self):
        results = self.search("bgblauth.json", terms="Schadenersatz", application="BgblAuth")
        first = results[0]
        self.assertTrue(first["gazette"].startswith("BGBl. "), first["gazette"])
        self.assertIn(first["gazette"], first["citation"])


if __name__ == "__main__":
    unittest.main()
