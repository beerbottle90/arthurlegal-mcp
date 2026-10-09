"""page_size: exactly the sizes RIS accepts, `limit` for fewer, upstream errors surface.

Checked live on 2026-10-09: DokumenteProSeite accepts Ten, Twenty, Fifty and
OneHundred only. "Five", "Fifteen", "TwentyFive" and numerals fail RIS's schema
validation — reported inside an HTTP 200 as ``OgdSearchResult.Error``, which
the client read as zero hits. The same silent zero hid that LrKons (provincial
law) was sent to the Bundesrecht endpoint; it lives under Landesrecht.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import retrieval  # noqa: E402
import ris  # noqa: E402
from mcpcore import McpError  # noqa: E402


def _load_server():
    # Loaded under its own name: every backend has a server.py.
    spec = importlib.util.spec_from_file_location(
        "at_server_under_test", os.path.join(ROOT, "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = _load_server()


def _decision(i: int) -> dict:
    doc = "JJT_20260101_OGH0002_%04d" % i
    return {"Data": {"Metadaten": {
        "Technisch": {"ID": doc, "Applikation": "Justiz", "Organ": "OGH"},
        "Allgemein": {"DokumentUrl": "https://ogd.ris.bka.gv.at/Dokument.wxe?"
                                     "Abfrage=Justiz&Dokumentnummer=%s" % doc},
        "Judikatur": {"Dokumenttyp": "Text", "Geschaeftszahl": {"item": "%dOb%d/26a" % (1 + i % 9, i)},
                      "Normen": {"item": "ABGB §1295"}, "Entscheidungsdatum": "2026-01-%02d" % (1 + i),
                      "EuropeanCaseLawIdentifier": "ECLI:AT:OGH0002:2026:00%dOB%05d.26A.0101.000" % (1 + i % 9, i),
                      "Justiz": {"Gericht": "OGH", "Rechtsgebiete": {"item": "Zivilrecht"}}}}}}


PAGE_OF_TEN = {"OgdSearchResult": {"OgdDocumentResults": {
    "Hits": {"@pageNumber": "1", "@pageSize": "10", "#text": "11602"},
    "OgdDocumentReference": [_decision(i) for i in range(10)]}}}

# Verbatim shape of RIS's answer to DokumenteProSeite=Five.
SCHEMA_ERROR = {"OgdSearchResult": {"Error": {
    "Applikation": "Justiz",
    "Message": "soap:Client. Schema Validation Error: The "
               "'http://ris.bka.gv.at/ogd/V2_6:DokumenteProSeite' element is invalid - "
               "The value 'Five' is invalid according to its datatype "
               "'http://ris.bka.gv.at/ogd/V2_6:PageSize' - The Enumeration constraint failed."}}}


class FakeGet:
    def __init__(self, payload):
        self.payload, self.urls = payload, []

    def __call__(self, url, timeout=60):
        self.urls.append(url)
        return self.payload


class PageSizeTest(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch.object(retrieval, "_probe", lambda force=False: False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, handler, payload, **args):
        fake = FakeGet(payload)
        with mock.patch.object(ris, "_get", fake):
            return handler(args), fake.urls

    def test_schema_offers_exactly_the_sizes_ris_accepts_and_a_limit(self):
        for name in ("search_caselaw", "search_legislation"):
            props = next(t for t in SERVER.TOOLS if t.name == name).input_schema["properties"]
            self.assertEqual(props["page_size"]["enum"], [10, 20, 50, 100])
            self.assertIn("limit", props["page_size"]["description"])
            self.assertEqual(props["limit"]["maximum"], 100)

    def test_limit_returns_fewer_results_than_a_page(self):
        out, urls = self.call(SERVER._t_search_caselaw, PAGE_OF_TEN,
                              terms="Schadenersatz", page_size=10, limit=5)
        self.assertEqual(out["returned"], 5)
        self.assertEqual(len(out["results"]), 5)
        self.assertEqual(out["total_upstream"], 11602)
        self.assertIn("DokumenteProSeite=Ten", urls[-1])

    def test_an_unsupported_page_size_is_refused_with_the_way_out(self):
        with self.assertRaises(McpError) as ctx:
            self.call(SERVER._t_search_caselaw, PAGE_OF_TEN, terms="Schadenersatz", page_size=5)
        self.assertIn("limit", str(ctx.exception))

    def test_a_ris_error_object_is_an_error_not_zero_hits(self):
        with self.assertRaises(McpError) as ctx:
            self.call(SERVER._t_search_caselaw, SCHEMA_ERROR, terms="Schadenersatz")
        self.assertIn("Schema Validation Error", str(ctx.exception))

    def test_provincial_law_is_searched_under_landesrecht(self):
        page = {"OgdSearchResult": {"OgdDocumentResults": {
            "Hits": {"#text": "0"}, "OgdDocumentReference": []}}}
        _, urls = self.call(SERVER._t_search_legislation, page,
                            terms="Schadenersatz", application="LrKons")
        self.assertIn("/Landesrecht?", urls[-1])
        _, urls = self.call(SERVER._t_search_legislation, page,
                            terms="Schadenersatz", application="BrKons")
        self.assertIn("/Bundesrecht?", urls[-1])


if __name__ == "__main__":
    unittest.main()
