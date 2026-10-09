"""Acts carry their enactment date, never an invented 1 January (defect #4).

crawl.py wrote ``"date": "%d-01-01" % year`` for every Act, so ie_search_acts
gave No. 44 of 2024 the date 2024-01-01; it was enacted on 12 November 2024.
The Statute Book publishes the date in the Act's own page metadata
(``eli:date_document``) and in the long title ("[12th November, 2024]").

    python -m unittest discover -s tests          (from ie-statutebook-mcp/)
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from unittest import mock

import ie_fakes
import retrieval
import statutebook


class EnactmentDateTests(unittest.TestCase):
    def test_date_from_page_metadata(self):
        self.assertEqual(statutebook._enacted_date(ie_fakes.page("act_2024_44_contents.html.gz")),
                         "2024-11-12")

    def test_date_from_long_title_when_metadata_is_missing(self):
        page = re.sub(r'<meta[^>]*eli:date_document[^>]*>', "",
                      ie_fakes.page("act_2024_44_contents.html.gz"))
        page = page.replace('"legislationDate"', '"x"')
        self.assertEqual(statutebook._enacted_date(page), "2024-11-12")

    def test_long_title_quirks_seen_in_the_index(self):
        # Tag boundaries inside the date, as the 2022 and 2023 pages render.
        self.assertEqual(statutebook.date_from_text("... matters. [2 nd Jun e, 2022] Be it"),
                         "2022-06-02")
        self.assertEqual(statutebook.date_from_text("... matters. [11 thJuly , 2023] Be it"),
                         "2023-07-11")
        self.assertEqual(statutebook.date_from_text("no date here [No. 38 of 2014]"), "")


class FetchDateTests(unittest.TestCase):
    def setUp(self):
        self.fake = ie_fakes.FakeStatuteBook(also_as=[1])
        p = mock.patch.object(statutebook, "_fetch", self.fake)
        p.start()
        self.addCleanup(p.stop)

    def test_get_act_reports_the_enactment_date(self):
        act = statutebook.StatuteBookClient().get_act(2024, 44)
        self.assertEqual(act["date_enacted"], "2024-11-12")

    def test_list_year_carries_the_date(self):
        acts = statutebook.StatuteBookClient().list_year(2024, probe_limit=3, miss_streak=2)
        self.assertEqual(acts[0]["number"], 1)
        self.assertEqual(acts[0]["date"], "2024-11-12")

    def test_crawl_stores_the_real_date_not_new_year(self):
        tmp = tempfile.mkdtemp()
        crawl = ie_fakes.load_module("ie_crawl_dates", "crawl.py")
        idx = retrieval.Index(os.path.join(tmp, "crawl.db"))
        with mock.patch.object(crawl.time, "sleep", lambda s: None):
            crawl.crawl(idx, 2024, 2024, probe_limit=3)
        doc = idx.get("IE/2024/act/1")
        self.assertEqual(doc["date"], "2024-11-12")
        self.assertEqual(doc["meta"]["date_source"], "eli:date_document")

    def test_crawl_leaves_an_unknown_date_empty(self):
        bare = re.sub(r'<meta[^>]*eli:date_document[^>]*>', "",
                      ie_fakes.page("act_2024_44_print.html.gz"))
        bare = bare.replace('"legislationDate"', '"x"').replace("November</i>, 2024]", "</i>]")
        contents = re.sub(r'<meta[^>]*eli:date_document[^>]*>', "",
                          ie_fakes.page("act_2024_44_contents.html.gz"))
        contents = contents.replace('"legislationDate"', '"x"').replace("November</i>, 2024]", "</i>]")
        fake = ie_fakes.FakeStatuteBook(also_as=[1], pages={
            "contents": contents, "print": bare,
            "section_1": ie_fakes.page("act_2024_44_section_1.html.gz")})
        tmp = tempfile.mkdtemp()
        crawl = ie_fakes.load_module("ie_crawl_nodate", "crawl.py")
        idx = retrieval.Index(os.path.join(tmp, "crawl.db"))
        with mock.patch.object(statutebook, "_fetch", fake), \
                mock.patch.object(crawl.time, "sleep", lambda s: None):
            crawl.crawl(idx, 2024, 2024, probe_limit=3)
        self.assertEqual(idx.get("IE/2024/act/1")["date"], "")


class SearchDateTests(unittest.TestCase):
    """Rows crawled before this fix still say 2024-01-01; search corrects them."""

    def setUp(self):
        p = mock.patch.object(retrieval, "_probe", lambda force=False: False)
        p.start()
        self.addCleanup(p.stop)
        tmp = tempfile.mkdtemp()
        self.server = ie_fakes.load_module("ie_server_dates", "server.py",
                                           os.path.join(tmp, "index.db"))
        idx = self.server._index
        legacy_body = ("Companies (Corporate Governance, Enforcement and Regulatory Provisions) "
                       "Act 2024 ... and the Registration of Business Names Act 1963 . "
                       "[12 th November , 2024] Be it enacted by the Oireachtas as follows:")
        idx.upsert({"ref": "IE/2024/act/44", "title": "Companies (Corporate Governance, "
                    "Enforcement and Regulatory Provisions) Act 2024", "body": legacy_body,
                    "date": "2024-01-01", "status": "as enacted",
                    "meta": {"year": 2024, "number": 44, "version": "enacted"}})
        idx.upsert({"ref": "IE/2024/act/45", "title": "Companies Undated Act 2024",
                    "body": "Companies text without a long-title date",
                    "date": "2024-01-01", "status": "as enacted",
                    "meta": {"year": 2024, "number": 45, "version": "enacted"}})
        idx.db.commit()
        idx.reindex_fts()

    def test_legacy_placeholder_is_replaced_or_blanked(self):
        out = self.server._t_search({"query": "Companies", "mode": "lexical"})
        dates = {r["ref"]: r["date"] for r in out["results"]}
        self.assertEqual(dates["IE/2024/act/44"], "2024-11-12")
        self.assertEqual(dates["IE/2024/act/45"], "")
        self.assertNotIn("2024-01-01", dates.values())
        undated = next(r for r in out["results"] if r["ref"] == "IE/2024/act/45")
        self.assertIn("date_note", undated)


if __name__ == "__main__":
    unittest.main()
