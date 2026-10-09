"""search_caselaw: Rechtssatz decision lists are shortened, the answer stays small.

Regression found live on 2026-10-09: terms "Schadenersatz", application
"Justiz", page_size 10 returned 256,833 characters. All ten hits were
Rechtssätze and each carried its full list of decisions with notes; one had
275. The payload below is built on the structure of that live answer, with the
same list sizes (13, 133, 52, 40, 20, 34, 36, 28, 16, 275).

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

LIST_SIZES = (13, 133, 52, 40, 20, 34, 36, 28, 16, 275)


def _decision(i: int, year: int) -> dict:
    gz = "%d Ob %d/%02d" % (1 + i % 10, 100 + i, year % 100)
    doc = "JJT_%d0101_OGH0002_%04d" % (year, i)
    # Notes vary from a citation to a long Beisatz, as in the live answer.
    note = ("Auch; Beisatz: Der Patient kann vom Rechtsträger der Krankenanstalt "
            "aufgrund des schlecht erfüllten Behandlungsvertrags Schadenersatz "
            "begehren. (T%d) " % i) * (1 + i % 3) + "Veröff: SZ %d/%d" % (year, i)
    return {"Geschaeftszahl": gz, "Dokumenttyp": "Text", "Gericht": "OGH",
            "Entscheidungsdatum": "%d-01-01" % year, "Anmerkung": note,
            "DokumentUrl": "https://ogd.ris.bka.gv.at/Dokument.wxe?Abfrage=Justiz"
                           "&Dokumentnummer=%s" % doc}


def _rechtssatz(n: int, rs: int) -> dict:
    decisions = [_decision(i, 1959 + (i * 67) // max(n, 1)) for i in range(n)]
    doc_id = "JJR_19590311_OGH0002_00%d" % rs
    base = "https://ogd.ris.bka.gv.at/Dokumente/Justiz/%s/%s" % (doc_id, doc_id)
    return {"Data": {
        "Metadaten": {
            "Technisch": {"ID": doc_id, "Applikation": "Justiz", "Organ": "OGH"},
            "Allgemein": {"Veroeffentlicht": "1997-06-15", "Geaendert": "2026-10-09",
                          "DokumentUrl": "https://ogd.ris.bka.gv.at/Dokument.wxe?"
                                         "Abfrage=Justiz&Dokumentnummer=%s" % doc_id},
            "Judikatur": {
                "Dokumenttyp": "Rechtssatz",
                # RIS packs every docket of the line into one string.
                "Geschaeftszahl": {"item": "; ".join(
                    d["Geschaeftszahl"].replace(" ", "") for d in decisions)},
                "Normen": {"item": ["ABGB §1295 IIc", "ABGB §1299 B", "AHG §1 Cd9"]},
                "Entscheidungsdatum": "2026-08-27",
                "EuropeanCaseLawIdentifier": "ECLI:AT:OGH0002:1959:RS%07d" % rs,
                "Justiz": {
                    "Rechtsgebiete": {"item": "Zivilrecht"},
                    "Gericht": "OGH",
                    "Rechtssatznummern": {"item": "RS%07d" % rs},
                    "Entscheidungstexte": {"item": decisions},
                },
            },
        },
        "Dokumentliste": {"ContentReference": {
            "ContentType": "MainDocument", "Name": "Hauptdokument",
            "Urls": {"ContentUrl": [
                {"DataType": t, "Url": "%s.%s" % (base, t.lower())}
                for t in ("Xml", "Html", "Rtf", "Pdf")]}}},
    }}


def _payload() -> dict:
    refs = [_rechtssatz(n, 22961 + k) for k, n in enumerate(LIST_SIZES)]
    return {"OgdSearchResult": {"OgdDocumentResults": {
        "Hits": {"@pageNumber": "1", "@pageSize": "10", "#text": "939"},
        "OgdDocumentReference": refs}}}


class RechtssatzLists(unittest.TestCase):

    def setUp(self):
        for target, attr, value in ((retrieval, "_probe", lambda force=False: False),
                                    (ris, "_get", lambda url, timeout=60: _payload())):
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.out = SERVER._t_search_caselaw(
            {"terms": "Schadenersatz", "application": "Justiz", "page_size": 10})

    def test_the_answer_stays_small(self):
        # As the MCP layer serialises it.
        size = len(json.dumps(self.out, ensure_ascii=False, indent=2))
        self.assertLess(size, 40000, size)

    def test_long_lists_show_first_and_latest_decisions_with_the_total(self):
        big = next(r for r in self.out["results"] if r["decisions_total"] == 275)
        self.assertLessEqual(len(big["decisions"]), 5)
        self.assertEqual(big["decisions_omitted"], 275 - len(big["decisions"]))
        dockets = [d["docket"] for d in big["decisions"]]
        self.assertEqual(dockets[0], "1 Ob 100/59")          # where the line starts
        self.assertEqual(dockets[-1], "5 Ob 374/25")          # the latest decision
        self.assertIn("275", big["note"])

    def test_short_lists_are_kept_whole(self):
        for r in self.out["results"]:
            if r["decisions_total"] <= 5:
                self.assertEqual(len(r["decisions"]), r["decisions_total"])
                self.assertNotIn("decisions_omitted", r)

    def test_docket_string_and_notes_are_bounded(self):
        for r in self.out["results"]:
            self.assertLess(len(r["docket"]), 120, r["docket"])
            for d in r["decisions"]:
                self.assertLessEqual(len(d["note"]), 201)
        big = next(r for r in self.out["results"] if r["decisions_total"] == 275)
        self.assertIn("+272", big["docket"])

    def test_citation_still_names_the_rechtssatz(self):
        first = self.out["results"][0]
        self.assertTrue(first["citation"].startswith("OGH RS00"), first["citation"])


if __name__ == "__main__":
    unittest.main()
