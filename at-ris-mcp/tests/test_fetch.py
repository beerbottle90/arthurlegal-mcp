"""fetch_document returns readable text by default, not page source.

Regression found live on 2026-10-09: fetching the HTML rendition with
max_chars=2500 returned only the page head and CSS. The XML rendition was
readable but still markup. The result's own ``url`` (Dokument.wxe / eli/)
answered 503, so it could not be fetched at all.

Fixtures are trimmed live documents: Rechtssatz RS0022961 (HTML and XML) and
§ 5 of the 5. Staatsvertragsdurchführungsgesetz (NOR12005821, HTML).

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from mcpcore import McpError  # noqa: E402


def _load_server():
    # Loaded under its own name: every backend has a server.py.
    spec = importlib.util.spec_from_file_location(
        "at_server_under_test", os.path.join(ROOT, "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = _load_server()

RS = "JJR_19590311_OGH0002_0010OB00066_5900000_001"
RS_FILE = "https://ogd.ris.bka.gv.at/Dokumente/Justiz/%s/%s" % (RS, RS)
NOR_FILE = "https://ogd.ris.bka.gv.at/Dokumente/Bundesnormen/NOR12005821/NOR12005821"


class _Response:
    def __init__(self, url, body, content_type):
        self._body, self.status, self._url = body, 200, url
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpen:
    """Stands in for urllib.request.urlopen: serves the fixtures by URL."""

    FILES = {RS_FILE + ".html": ("rs0022961.html", "text/html; charset=utf-8"),
             RS_FILE + ".xml": ("rs0022961.xml", "application/xml; charset=utf-8"),
             NOR_FILE + ".html": ("nor12005821.html", "text/html; charset=utf-8")}

    def __init__(self):
        self.urls = []

    def __call__(self, req, timeout=60):
        url = getattr(req, "full_url", req)
        self.urls.append(url)
        if url in self.FILES:
            name, ctype = self.FILES[url]
            with open(os.path.join(HERE, "fixtures", name), "rb") as fh:
                return _Response(url, fh.read(), ctype)
        # What the RIS web pages answer automated clients.
        raise urllib.error.HTTPError(url, 503, "Service Temporarily Unavailable", {}, None)


class FetchTest(unittest.TestCase):

    def fetch(self, **args):
        fake = FakeOpen()
        with mock.patch("urllib.request.urlopen", fake):
            out = SERVER._t_fetch(args)
        return out, fake.urls


class ReadableByDefault(FetchTest):

    def test_html_rendition_starts_with_the_document_not_the_page_head(self):
        out, _ = self.fetch(url=RS_FILE + ".html", max_chars=2500)
        text = out["text"]
        self.assertTrue(text.startswith("Gericht\nOGH"), text[:200])
        self.assertIn("Für die Schäden, welche den Patienten an Universitätskliniken", text)
        for noise in ("<", "@media", "{", "RIS Dokument"):
            self.assertNotIn(noise, text)
        self.assertEqual(out["format"], "html")

    def test_screen_reader_duplicates_are_dropped(self):
        out, _ = self.fetch(url=RS_FILE + ".html")
        self.assertIn("ABGB §1295 IIc", out["text"])
        self.assertNotIn("römisch", out["text"])
        law, _ = self.fetch(url=NOR_FILE + ".html")
        self.assertIn("§ 5.", law["text"])
        self.assertIn("(1) Wird in einem gerichtlichen Verfahren", law["text"])
        self.assertNotIn("Paragraph 5,", law["text"])
        self.assertNotIn("Absatz eins", law["text"])

    def test_xml_rendition_loses_markup_and_page_furniture(self):
        out, _ = self.fetch(url=RS_FILE + ".xml")
        text = out["text"]
        self.assertTrue(text.startswith("Gericht\nOGH"), text[:200])
        self.assertIn("Für die Schäden", text)
        self.assertNotIn("<", text)
        self.assertNotIn("Seite 1 von", text)
        self.assertNotIn("www.ris.bka.gv.at", text)
        self.assertEqual(out["format"], "xml")

    def test_raw_returns_the_file_as_served(self):
        out, _ = self.fetch(url=RS_FILE + ".html", raw=True, max_chars=200)
        self.assertTrue(out["text"].startswith("<!DOCTYPE html"), out["text"][:80])


class UrlsThatCannotBeReadDirectly(FetchTest):

    def test_pdf_and_rtf_are_read_from_the_html_rendition(self):
        for ext in (".pdf", ".rtf"):
            out, urls = self.fetch(url=RS_FILE + ext)
            self.assertEqual(urls[-1], RS_FILE + ".html")
            self.assertIn("Für die Schäden", out["text"])

    def test_result_url_of_a_decision_is_mapped_to_its_document_file(self):
        page = ("https://ogd.ris.bka.gv.at/Dokument.wxe?Abfrage=Justiz&Dokumentnummer=%s" % RS)
        out, urls = self.fetch(url=page)
        self.assertEqual(urls[-1], RS_FILE + ".html")
        self.assertEqual(out["fetched_url"], RS_FILE + ".html")
        self.assertIn("Rechtssatz", out["text"])

    def test_eli_url_of_a_federal_norm_is_mapped_to_its_document_file(self):
        out, urls = self.fetch(url="https://ogd.ris.bka.gv.at/eli/bgbl/1958/16/P5/NOR12005821")
        self.assertEqual(urls[-1], NOR_FILE + ".html")
        self.assertIn("§ 5.", out["text"])

    def test_unmappable_web_page_explains_the_503(self):
        with self.assertRaises(McpError) as ctx:
            self.fetch(url="https://ogd.ris.bka.gv.at/eli/bgbl/I/2026/58/20260728")
        self.assertIn("formats", str(ctx.exception))

    def test_non_ris_urls_are_still_refused(self):
        with self.assertRaises(McpError):
            self.fetch(url="https://example.org/Dokumente/x.html")


if __name__ == "__main__":
    unittest.main()
