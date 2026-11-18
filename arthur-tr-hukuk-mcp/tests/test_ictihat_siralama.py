"""Case-law ordering and honest shortlist reporting -- offline.

Run: python -m unittest tests.test_ictihat_siralama

Until 0.8.0 every Bedesten case-law search carried ``sortFields=KARAR_TARIHI``.
A question then got the newest decisions sharing its words, and the shortlist
reranker ranked those. Measured 2026-11-18 on six plain-word queries about one
enforcement-law question: by date, a controlling decision reached the first ten
for none of them; with no sort field, for five, first in four. These tests pin
the request shape that measurement depends on, and the notes that tell a caller
what was not read.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import server  # noqa: E402
from sources import bedesten_ictihat  # noqa: E402


def _row(n: int):
    return {"documentId": str(n), "itemType": {"name": "YARGITAYKARARI", "description": "Yargıtay Kararı"},
            "birimAdi": "8. Hukuk Dairesi", "esasNo": "2014/%d" % n, "kararNo": "2015/%d" % n,
            "kararTarihiStr": "19.02.2015"}


class FakeBedesten:
    """Keeps every request body; answers ``total`` hits and ``rows`` rows."""

    def __init__(self, total: int = 1230, rows: int = 10):
        self.total, self.rows, self.sent = total, rows, []

    def post_json(self, path, payload):
        self.sent.append(dict(payload["data"]))
        return {"metadata": {"FMTY": "SUCCESS"},
                "data": {"total": self.total, "emsalKararList": [_row(i) for i in range(1, self.rows + 1)]}}


class OrderingTest(unittest.TestCase):
    def run_search(self, fake=None, **args):
        fake = fake or FakeBedesten()
        saved = bedesten_ictihat._http
        bedesten_ictihat._http = fake
        try:
            return bedesten_ictihat.search(args), fake.sent
        finally:
            bedesten_ictihat._http = saved

    def test_default_sends_no_sort_field(self):
        out, sent = self.run_search(query="+takas +haciz")
        self.assertNotIn("sortFields", sent[0])
        self.assertNotIn("sortDirection", sent[0])
        self.assertEqual(out["siralama"], "ilgi")
        self.assertIn("sort='yeni'", out["siralama_notu"])

    def test_newest_and_oldest_are_one_argument_away(self):
        for sort, yon, ad in (("yeni", "desc", "yeni"), ("eski", "asc", "eski"),
                              ("desc", "desc", "yeni"), ("asc", "asc", "eski")):   # last two: pre-0.8.0 spellings
            out, sent = self.run_search(query="+takas +haciz", sort=sort)
            self.assertEqual(sent[0]["sortFields"], ["KARAR_TARIHI"], sort)
            self.assertEqual(sent[0]["sortDirection"], yon, sort)
            self.assertEqual(out["siralama"], ad, sort)
            self.assertIn("sort='ilgi'", out["siralama_notu"], sort)

    def test_an_unknown_order_is_refused_before_any_request(self):
        out, sent = self.run_search(query="takas", sort="alaka")
        self.assertIn("error", out)
        self.assertEqual(sent, [])

    def test_no_ordering_note_when_everything_fits_on_the_page(self):
        out, _ = self.run_search(FakeBedesten(total=5, rows=5), query="+takas +haciz")
        self.assertNotIn("siralama_notu", out)

    def test_schema_offers_the_three_orders_and_defaults_to_relevance(self):
        sort = bedesten_ictihat.SEARCH_SCHEMA["properties"]["sort"]
        self.assertEqual(sort["enum"], ["ilgi", "yeni", "eski"])
        self.assertEqual(sort["default"], "ilgi")

    def test_a_docket_number_is_searched_without_its_slash(self):
        out, sent = self.run_search(query="2014/27629")
        self.assertEqual(sent[0]["phrase"], '"2014 27629"')        # two plain words: the ladder's phrase rung
        self.assertIn("2014 27629", out["sorgu_notu"])
        out, sent = self.run_search(query='"2014/27629"')
        self.assertEqual(sent[0]["phrase"], '"2014 27629"')
        out, sent = self.run_search(query="takas haciz")
        self.assertNotIn("sorgu_notu", out)

    def test_unsigned_terms_beside_a_quote_get_the_all_required_form(self):
        out, sent = self.run_search(query='"takas mahsup" "dosya alacağı" haciz')
        self.assertEqual(sent[0]["phrase"], '"takas mahsup" "dosya alacağı" haciz')   # still sent as written
        self.assertEqual(out["onerilen_sorgu"], '+"takas mahsup" +"dosya alacağı" +haciz')
        self.assertIn("VEYA", out["arama_notu"])
        out, _ = self.run_search(query='+"takas mahsup" haciz')
        self.assertEqual(out["onerilen_sorgu"], '+"takas mahsup" +haciz')

    def test_queries_whose_joins_are_already_chosen_get_no_suggestion(self):
        for q in ('"takas mahsup"', '+"takas mahsup" +haciz', '"takas mahsup" AND haciz', "+takas -ceza", "takas"):
            out, _ = self.run_search(query=q)
            self.assertNotIn("onerilen_sorgu", out, q)


class ShortlistTest(unittest.TestCase):
    """ictihat_semantik_ara says how much of the candidate set it never read."""

    def run_tool(self, total, rows, **args):
        fake = FakeBedesten(total=total, rows=rows)
        saved = (bedesten_ictihat._http, bedesten_ictihat.get, server.retrieval.semantic_rerank)
        bedesten_ictihat._http = fake
        bedesten_ictihat.get = lambda a: {"text": "karar metni %s" % a["document_id"]}
        server.retrieval.semantic_rerank = lambda q, docs, fields, limit: {"results": [dict(d) for d in docs],
                                                                           "method": "semantic"}
        try:
            return server.t_ictihat_semantik(args), fake.sent
        finally:
            bedesten_ictihat._http, bedesten_ictihat.get, server.retrieval.semantic_rerank = saved

    def test_candidates_come_from_the_relevance_page(self):
        out, sent = self.run_tool(1230, 10, query="haciz takası engeller mi", initial_keyword="+takas +haciz")
        self.assertNotIn("sortFields", sent[0])
        self.assertEqual(out["fetched"], 5)

    def test_unread_candidates_are_reported(self):
        out, _ = self.run_tool(1230, 10, query="haciz takası engeller mi", initial_keyword="+takas +haciz")
        self.assertIn("1230 adaydan yalnız ilk 5 karar okundu", out["uyari"])
        self.assertIn("karar olmadığını göstermez", out["uyari"])

    def test_no_warning_when_every_candidate_was_read(self):
        out, _ = self.run_tool(3, 3, query="haciz takası engeller mi", initial_keyword="+takas +haciz")
        self.assertNotIn("uyari", out)

    def test_tool_text_does_not_promise_discovery(self):
        tools = {t.name: t for t in server.TOOLS}
        self.assertIn("KEŞİF ARACI DEĞİLDİR", tools["ictihat_semantik_ara"].description)
        self.assertIn("ilgiye göre", tools["ictihat_ara"].description)
        self.assertIn("İÇTİHAT ARAMASI", server.INSTRUCTIONS)
        self.assertIn("ARAMA YÖNTEMİ", server.t_rehber({})["guide"])


if __name__ == "__main__":
    unittest.main()
