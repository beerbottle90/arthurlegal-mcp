"""search_eu_law: word-level title matching, a wide candidate pool, no duplicates.

Regressions found live on 2026-10-09:

- Undated "general data protection regulation": the five first results were all
  2024-2025 documents and 32016R0679 was missing. The SPARQL kept only the ten
  newest matches (ORDER BY DESC(?date) LIMIT 10) and reranked those.
- "General Data Protection Regulation repealing Directive 95/46/EC" inside 2016
  returned nothing although every word is in the GDPR title: the whole query
  was one substring.
- "Schrems" listed 62018CJ0311 twice (one CELEX on two Cellar works) and, within
  2015-2020, never reached C-362/14 (62014CJ0362).

Fixtures are trimmed live CELLAR answers; nothing here touches the network and
the embeddings backend is switched off, so ranking is the BM25 fallback.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import cellar  # noqa: E402
import retrieval  # noqa: E402


def _load_server():
    # Loaded under its own name: every backend has a server.py.
    spec = importlib.util.spec_from_file_location(
        "eu_server_under_test", os.path.join(ROOT, "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = _load_server()


def _fixture(name: str):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


class FakeSparql:
    """Stands in for ``cellar._sparql``: records the query, answers from a fixture."""

    def __init__(self, name: str):
        self.data = _fixture(name)
        self.queries = []

    def __call__(self, query, timeout=120):
        self.queries.append(query)
        return self.data


class SearchTest(unittest.TestCase):

    def setUp(self):
        # Keyword fallback only: no embeddings endpoint is contacted.
        patcher = mock.patch.object(retrieval, "_probe", lambda force=False: False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def search(self, fixture: str, **args):
        fake = FakeSparql(fixture)
        with mock.patch.object(cellar, "_sparql", fake):
            out = SERVER._t_search(args)
        return out, fake.queries[-1]


class WordMatching(SearchTest):

    def test_every_title_word_is_matched_on_its_own(self):
        out, sparql = self.search(
            "gdpr_long_2016.json",
            query="General Data Protection Regulation repealing Directive 95/46/EC",
            date_from="2016-01-01", date_to="2016-12-31")
        self.assertNotIn("general data protection regulation repealing", sparql.lower())
        for word in ("general", "data", "protection", "regulation", "repealing",
                     "directive", "95/46/ec"):
            self.assertIn('"%s"' % word, sparql)
        self.assertIn('"2016-01-01"', sparql)
        self.assertIn('"2016-12-31"', sparql)
        self.assertEqual(out["results"][0]["celex"], "32016R0679")

    def test_punctuation_and_one_letter_words_cannot_break_the_expression(self):
        _, sparql = self.search("schrems.json",
                                query='Schrems v. Facebook, "Ireland" (C-498/16) d\'Italia')
        expression = re.search(r"bif:contains '([^']*)'", sparql).group(1)
        self.assertEqual(
            expression,
            '"schrems" AND "facebook" AND "ireland" AND "c-498/16" AND "italia"')

    def test_a_query_without_words_is_refused(self):
        with mock.patch.object(cellar, "_sparql", FakeSparql("schrems.json")):
            with self.assertRaises(SERVER.McpError):
                SERVER._t_search({"query": " v . , "})


class CandidatePool(SearchTest):

    def test_pool_is_not_the_ten_newest_matches(self):
        _, sparql = self.search("gdpr_words.json", query="general data protection regulation")
        self.assertNotIn("ORDER BY DESC(?date)", sparql)
        limit = int(re.search(r"LIMIT (\d+)", sparql).group(1))
        self.assertGreaterEqual(limit, 200)

    def test_gdpr_is_among_the_first_results_for_its_own_name(self):
        out, _ = self.search("gdpr_words.json", query="general data protection regulation")
        top = [r["celex"] for r in out["results"][:3]]
        self.assertIn("32016R0679", top)
        self.assertEqual(out["results"][0]["group"], "legislation")

    def test_case_law_query_lists_c_362_14_once(self):
        out, _ = self.search("schrems.json", query="Schrems")
        celexes = [r["celex"] for r in out["results"]]
        self.assertEqual(len(celexes), len(set(celexes)), celexes)
        self.assertIn("62014CJ0362", celexes)
        # The Court's own documents come before OJ notices about them.
        groups = [r["group"] for r in out["results"]]
        self.assertEqual(groups[0], "case_law")
        self.assertEqual(groups, sorted(groups, key=cellar.GROUPS.index))

    def test_limit_applies_after_ranking(self):
        out, _ = self.search("schrems.json", query="Schrems", limit=3)
        self.assertEqual(out["returned"], 3)
        self.assertEqual(out["candidates"], 20)


if __name__ == "__main__":
    unittest.main()
