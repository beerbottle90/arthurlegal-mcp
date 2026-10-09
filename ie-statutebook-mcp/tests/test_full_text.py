"""get_act returns the Act's text, not its table of contents (defect #5).

/eli/{y}/act/{n}/enacted/en/html is the Act's contents page: for No. 44 of 2024
it gave 8,959 characters of section titles, the long title — and no section
text at all. The text is the page the site links as "View Full Act",
/eli/{y}/act/{n}/enacted/en/print.html. Every page also opened with ~1,600
characters of site menu in English and Irish ("Produced by the Office of the
Attorney General ... Legislation Acts of the Oireachtas ... Baile ...") and
closed with the footer; both came through into ``text``.

    python -m unittest discover -s tests          (from ie-statutebook-mcp/)
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import ie_fakes
import retrieval
import statutebook

CHROME = ("Produced by the Office of the Attorney General", "Acts of the Oireachtas Statutory",
          "Táirgthe ag Oifig an Ard-Aighne", "Advanced Search", "Privacy Statement",
          "Permanent Page URL")
SECTION_1 = ("(1) This Act may be cited as the Companies (Corporate Governance, "
             "Enforcement and Regulatory Provisions) Act 2024.")
SECTION_90 = "Section 16 of the Registration of Business Names Act 1963 is amended"


class FullActTests(unittest.TestCase):
    def setUp(self):
        self.fake = ie_fakes.FakeStatuteBook(also_as=[1])
        p = mock.patch.object(statutebook, "_fetch", self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.client = statutebook.StatuteBookClient()

    def test_act_text_has_the_sections(self):
        act = self.client.get_act(2024, 44, max_chars=200000)
        self.assertIn(SECTION_1, act["text"])
        self.assertIn(SECTION_90, act["text"])
        self.assertGreater(act["length_chars"], 60000)

    def test_no_site_menu_or_footer(self):
        act = self.client.get_act(2024, 44, max_chars=200000)
        for phrase in CHROME:
            self.assertNotIn(phrase, act["text"], phrase)
        self.assertTrue(act["text"].startswith("Number 44 of 2024"), act["text"][:80])

    def test_section_index(self):
        act = self.client.get_act(2024, 44)
        self.assertEqual(act["sections_total"], 90)
        self.assertEqual(act["sections"][0], {"number": "1", "title": "Short title and commencement"})
        self.assertEqual(act["sections"][-1]["number"], "90")
        self.assertEqual(act["schedules"], ["SCHEDULE 1", "SCHEDULE 2"])

    def test_long_text_pages_with_offset(self):
        first = self.client.get_act(2024, 44, max_chars=20000)
        self.assertEqual(len(first["text"]), 20000)
        self.assertEqual(first["next_offset"], 20000)
        self.assertIn("truncated", first)
        second = self.client.get_act(2024, 44, max_chars=20000, offset=first["next_offset"])
        whole = self.client.get_act(2024, 44, max_chars=200000)
        self.assertEqual(first["text"] + second["text"], whole["text"][:40000])

    def test_section_text_has_no_site_menu(self):
        sec = self.client.get_section(2024, 44, "1")
        self.assertIn(SECTION_1, sec["text"])
        for phrase in CHROME:
            self.assertNotIn(phrase, sec["text"], phrase)

    def test_crawl_indexes_the_text_not_the_contents_page(self):
        tmp = tempfile.mkdtemp()
        crawl = ie_fakes.load_module("ie_crawl_text", "crawl.py")
        idx = retrieval.Index(os.path.join(tmp, "crawl.db"))
        with mock.patch.object(crawl.time, "sleep", lambda s: None):
            crawl.crawl(idx, 2024, 2024, probe_limit=3)
        body = idx.get("IE/2024/act/1")["body"]
        self.assertIn(SECTION_1, body)
        self.assertNotIn("Produced by the Office of the Attorney General", body)


if __name__ == "__main__":
    unittest.main()
