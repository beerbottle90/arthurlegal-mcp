"""search_legislation: consolidated law (BWB) by default, gazettes only on request.

Regression found live on 2026-10-09: "Burgerlijk Wetboek Boek 6" returned 228
records from the KOOP publications repository, the first three of them
Staatscourant notices ("Consignatie van gelden"), linked to
repository.overheid.nl although the tool promised wetten.overheid.nl. The
consolidated-law service (BWB) answers the same title with BWBR0005289.

Fixtures are trimmed live responses; nothing here touches the network.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
# server.py opens its index at import time; keep it out of the working tree.
os.environ["INDEX_PATH"] = os.path.join(tempfile.mkdtemp(), "index.db")

import rechtspraak  # noqa: E402
from mcpcore import McpError  # noqa: E402


def _load_server():
    # Loaded under its own name: every backend has a server.py.
    spec = importlib.util.spec_from_file_location(
        "nl_server_under_test", os.path.join(ROOT, "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = _load_server()


def _fixture(name: str) -> str:
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return fh.read()


class FakeFetch:
    """Stands in for ``rechtspraak._fetch``: records URLs, answers from fixtures."""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def __call__(self, url, timeout=90):
        self.urls.append(url)
        for needle, name in self.routes.items():
            if needle in url:
                return _fixture(name)
        raise AssertionError("unexpected upstream URL: %s" % url)

    def cql(self) -> str:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.urls[-1]).query)
        return query["query"][0]


BWB = "zoekservice.overheid.nl/sru/Search"
REPOSITORY = "repository.overheid.nl/sru"


class ConsolidatedByDefault(unittest.TestCase):

    def test_title_finds_the_code_itself_with_a_permanent_wetten_link(self):
        fake = FakeFetch({BWB: "bwb_bw6_current.xml"})
        with mock.patch.object(rechtspraak, "_fetch", fake):
            out = SERVER._t_search_legislation({"query": "Burgerlijk Wetboek Boek 6"})

        self.assertIn("x-connection=BWB", fake.urls[-1])
        first = out["results"][0]
        self.assertEqual(first["identifier"], "BWBR0005289")
        self.assertEqual(first["title"], "Burgerlijk Wetboek Boek 6")
        self.assertEqual(first["url"], "https://wetten.overheid.nl/BWBR0005289")
        self.assertEqual(first["citation"], "Burgerlijk Wetboek Boek 6 (BWBR0005289)")
        for item in out["results"]:
            self.assertTrue(item["identifier"].startswith("BWBR"), item)
            self.assertTrue(item["url"].startswith("https://wetten.overheid.nl/BWBR"), item)

    def test_only_the_version_in_force_is_asked_for(self):
        fake = FakeFetch({BWB: "bwb_bw6_current.xml"})
        with mock.patch.object(rechtspraak, "_fetch", fake):
            SERVER._t_search_legislation({"query": "Burgerlijk Wetboek Boek 6"})
        cql = fake.cql()
        self.assertIn('overheidbwb.titel all "Burgerlijk Wetboek Boek 6"', cql)
        self.assertRegex(cql, r'overheidbwb\.geldigheidsdatum = "\d{4}-\d{2}-\d{2}"')

    def test_versions_of_one_regulation_collapse_to_one_result(self):
        # Without a validity date BWB returns one record per version (toestand).
        fake = FakeFetch({BWB: "bwb_bw6_versions.xml"})
        with mock.patch.object(rechtspraak, "_fetch", fake):
            out = SERVER._t_search_legislation({"query": "Burgerlijk Wetboek Boek 6"})
        self.assertEqual([r["identifier"] for r in out["results"]], ["BWBR0005289"])

    def test_as_of_selects_the_version_valid_on_that_day(self):
        fake = FakeFetch({BWB: "bwb_bw6_current.xml"})
        with mock.patch.object(rechtspraak, "_fetch", fake):
            out = SERVER._t_search_legislation(
                {"query": "Burgerlijk Wetboek Boek 6", "as_of": "2001-01-01"})
        self.assertIn('overheidbwb.geldigheidsdatum = "2001-01-01"', fake.cql())
        self.assertEqual(out["as_of"], "2001-01-01")

    def test_a_malformed_as_of_is_refused_not_guessed(self):
        with mock.patch.object(rechtspraak, "_fetch", FakeFetch({})):
            with self.assertRaises(McpError):
                SERVER._t_search_legislation(
                    {"query": "Burgerlijk Wetboek Boek 6", "as_of": "01-01-2001"})

    def test_a_bwbr_identifier_is_looked_up_directly(self):
        fake = FakeFetch({BWB: "bwb_bw6_current.xml"})
        with mock.patch.object(rechtspraak, "_fetch", fake):
            SERVER._t_search_legislation({"query": "bwbr0005289"})
        self.assertIn('dcterms.identifier = "BWBR0005289"', fake.cql())

    def test_an_sru_diagnostic_is_an_error_not_an_empty_result(self):
        fake = FakeFetch({BWB: "bwb_diagnostic.xml"})
        with mock.patch.object(rechtspraak, "_fetch", fake):
            with self.assertRaises(McpError) as ctx:
                SERVER._t_search_legislation({"query": "Burgerlijk Wetboek Boek 6"})
        self.assertIn("invalid format", str(ctx.exception))


class OfficialPublicationsOnRequest(unittest.TestCase):

    def test_gazettes_are_a_separate_source(self):
        fake = FakeFetch({REPOSITORY: "repository_bw6.xml"})
        with mock.patch.object(rechtspraak, "_fetch", fake):
            out = SERVER._t_search_legislation(
                {"query": "Burgerlijk Wetboek Boek 6", "source": "official_publications"})
        self.assertIn(REPOSITORY, fake.urls[-1])
        first = out["results"][0]
        self.assertEqual(first["identifier"], "stcrt-2025-5530")
        # The publication's own page, never a wetten.overheid.nl path it does not have.
        self.assertEqual(first["url"], "https://zoek.officielebekendmakingen.nl/stcrt-2025-5530.html")
        self.assertEqual(out["source"], "official_publications")

    def test_schema_offers_both_sources_and_defaults_to_consolidated(self):
        tool = next(t for t in SERVER.TOOLS if t.name == "search_legislation")
        source = tool.input_schema["properties"]["source"]
        self.assertEqual(source["default"], "consolidated")
        self.assertEqual(sorted(source["enum"]), ["consolidated", "official_publications"])
        self.assertIn("as_of", tool.input_schema["properties"])
        self.assertIn("wetten.overheid.nl", tool.description)


if __name__ == "__main__":
    unittest.main()
