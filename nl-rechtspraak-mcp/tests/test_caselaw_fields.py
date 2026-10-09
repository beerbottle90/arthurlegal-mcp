"""Decision fields Rechtspraak publishes but the server returned empty.

Regressions found live on 2026-10-09:

- get_decision returned an empty ``abstract`` for ECLI:NL:HR:2026:919 although
  the official record has an inhoudsindicatie. ``dcterms:abstract`` there is
  only a pointer (``resourceIdentifier="../../rs:inhoudsindicatie"``); the
  text sits in the ``<inhoudsindicatie>`` element.
- search_caselaw results had an empty ``court``. Summary crawls never stored
  it, although every Atom title carries it:
  "ECLI:NL:HR:2026:919, Hoge Raad, 12-06-2026, 24/04627".

Fixtures are trimmed live responses; nothing here touches the network.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["INDEX_PATH"] = os.path.join(tempfile.mkdtemp(), "index.db")

import crawl  # noqa: E402
import rechtspraak  # noqa: E402
import retrieval  # noqa: E402


def _load_server():
    spec = importlib.util.spec_from_file_location(
        "nl_server_under_test", os.path.join(ROOT, "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = _load_server()


def _fixture(name: str) -> str:
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return fh.read()


def _serve(name: str):
    return lambda url, timeout=90: _fixture(name)


def _summary_index() -> retrieval.Index:
    """An index as summary crawls built it before this fix: no court stored."""
    index = retrieval.Index(os.path.join(tempfile.mkdtemp(), "index.db"))
    rows = [
        ("ECLI:NL:HR:2026:919", "ECLI:NL:HR:2026:919, Hoge Raad, 12-06-2026, 24/04627",
         "Onrechtmatige daad. Oneerlijke handelspraktijken, misleidende en "
         "vergelijkende reclame, art. 6:194a BW, ongeoorloofdheid, uitleg uitspraak, "
         "motivering.", "2026-06-12"),
        ("ECLI:NL:CBB:2026:227",
         "ECLI:NL:CBB:2026:227, College van Beroep voor het bedrijfsleven, 02-06-2026, 24/719",
         "Hoger beroep. Wet handhaving consumentenbescherming. Bestuurlijke boete "
         "van de ACM voor oneerlijke en misleidende handelspraktijken.", "2026-06-02"),
    ]
    for ecli, title, body, day in rows:
        index.upsert({"ref": ecli, "title": title, "body": body, "lang": "nl",
                      "date": day, "court": "", "citation": ecli,
                      "url": "https://uitspraken.rechtspraak.nl/details?id=%s" % ecli,
                      "meta": {"depth": "summary"}})
    index.db.commit()
    index.reindex_fts()
    return index


class Abstract(unittest.TestCase):

    def test_get_decision_returns_the_inhoudsindicatie(self):
        with mock.patch.object(rechtspraak, "_fetch", _serve("hr_2026_919.xml")):
            out = SERVER._t_get_decision({"ecli": "ECLI:NL:HR:2026:919"})
        self.assertTrue(out["abstract"].startswith(
            "Onrechtmatige daad. Oneerlijke handelspraktijken"), out["abstract"])
        self.assertEqual(out["court"], "Hoge Raad")
        self.assertIn("HOGE RAAD DER NEDERLANDEN", out["text"])


class Court(unittest.TestCase):

    def setUp(self):
        # Keyword channels only: no embeddings endpoint is contacted.
        patcher = mock.patch.object(retrieval, "_probe", lambda force=False: False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_court_is_read_from_the_atom_title(self):
        self.assertEqual(rechtspraak.court_from_title(
            "ECLI:NL:CBB:2026:227, College van Beroep voor het bedrijfsleven, 02-06-2026, 24/719"),
            "College van Beroep voor het bedrijfsleven")
        # The content record writes the same title with different punctuation.
        self.assertEqual(rechtspraak.court_from_title(
            "ECLI:NL:HR:2026:919 Hoge Raad , 12-06-2026 / 24/04627"), "Hoge Raad")
        self.assertEqual(rechtspraak.court_from_title("no title"), "")

    def test_browse_results_carry_the_court(self):
        with mock.patch.object(rechtspraak, "_fetch", _serve("atom_2026-06-12.xml")):
            out = SERVER._t_browse_caselaw({"date": "2026-06-12", "limit": 3})
        self.assertEqual([r["court"] for r in out["results"]], ["Hoge Raad"] * 3)

    def test_search_fills_the_court_for_rows_indexed_without_it(self):
        with mock.patch.object(SERVER, "_index", _summary_index()):
            out = SERVER._t_search_caselaw({"query": "oneerlijke handelspraktijken"})
        courts = {r["ref"]: r["court"] for r in out["results"]}
        self.assertEqual(courts["ECLI:NL:HR:2026:919"], "Hoge Raad")
        self.assertEqual(courts["ECLI:NL:CBB:2026:227"],
                         "College van Beroep voor het bedrijfsleven")

    def test_court_filter_says_when_the_index_cannot_honour_it(self):
        with mock.patch.object(SERVER, "_index", _summary_index()):
            out = SERVER._t_search_caselaw(
                {"query": "oneerlijke handelspraktijken", "court": "Hoge Raad"})
        self.assertIn("court_filter_warning", out)
        self.assertIn("--backfill-court", out["court_filter_warning"])

    def test_summary_crawl_stores_the_court(self):
        index = retrieval.Index(os.path.join(tempfile.mkdtemp(), "index.db"))
        with mock.patch.object(rechtspraak, "_fetch", _serve("atom_2026-06-12.xml")):
            crawl.crawl(index, "2026-06-12", "2026-06-12", pause=0)
        self.assertEqual(index.get("ECLI:NL:HR:2026:920")["court"], "Hoge Raad")

    def test_backfill_repairs_an_existing_index_without_network(self):
        index = _summary_index()
        with mock.patch.object(rechtspraak, "_fetch", side_effect=AssertionError("network")):
            fixed = crawl.backfill_court(index)
        self.assertEqual(fixed, 2)
        self.assertEqual(index.get("ECLI:NL:HR:2026:919")["court"], "Hoge Raad")
        with mock.patch.object(SERVER, "_index", index):
            out = SERVER._t_search_caselaw(
                {"query": "oneerlijke handelspraktijken", "court": "Hoge Raad"})
        self.assertEqual([r["ref"] for r in out["results"]], ["ECLI:NL:HR:2026:919"])
        self.assertNotIn("court_filter_warning", out)


if __name__ == "__main__":
    unittest.main()
