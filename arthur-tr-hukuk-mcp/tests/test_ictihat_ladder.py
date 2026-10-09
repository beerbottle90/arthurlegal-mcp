"""Bedesten query ladder -- offline. Run: python -m pytest tests/ -q  (or python -m unittest tests.test_ictihat_ladder).

Bedesten joins bare words with OR, so a plain multi-word query is tried as a
phrase first, then with every word required, and only then as sent.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sources import bedesten_ictihat  # noqa: E402


class FakeBedesten:
    """Records every phrase sent and answers with the total configured for it."""

    def __init__(self, totals):
        self.totals = totals
        self.sent = []

    def post_json(self, path, payload):
        phrase = payload["data"]["phrase"]
        self.sent.append(phrase)
        total = self.totals.get(phrase, 0)
        rows = [{"documentId": "1", "itemType": {"name": "YARGITAYKARARI", "description": "Yargıtay Kararı"},
                 "birimAdi": "3. Hukuk Dairesi", "esasNo": "2025/1", "kararNo": "2026/1",
                 "kararTarihiStr": "21.05.2026"}] if total else []
        return {"metadata": {"FMTY": "SUCCESS"}, "data": {"total": total, "emsalKararList": rows}}


class LadderTest(unittest.TestCase):
    def run_search(self, totals, **args):
        fake = FakeBedesten(totals)
        saved = bedesten_ictihat._http
        bedesten_ictihat._http = fake
        try:
            return bedesten_ictihat.search(args), fake.sent
        finally:
            bedesten_ictihat._http = saved

    def test_plain_multiword_query_tries_the_phrase_first(self):
        out, sent = self.run_search({'"kira tespit davası"': 2127}, query="kira tespit davası")
        self.assertEqual(sent, ['"kira tespit davası"'])
        self.assertEqual(out["total"], 2127)
        self.assertEqual(out["arama_modu"], "tam ifade")
        self.assertEqual(out["uygulanan_sorgu"], '"kira tespit davası"')

    def test_widens_to_every_word_when_the_phrase_finds_nothing(self):
        out, sent = self.run_search({"+kira +tespit +davası": 5}, query="kira tespit davası")
        self.assertEqual(sent, ['"kira tespit davası"', "+kira +tespit +davası"])
        self.assertEqual(out["arama_modu"], "kelimelerin hepsi")

    def test_falls_back_to_the_query_as_sent_last(self):
        out, sent = self.run_search({}, query="kira tespit davası")
        self.assertEqual(sent, ['"kira tespit davası"', "+kira +tespit +davası", "kira tespit davası"])
        self.assertEqual(out["total"], 0)
        self.assertEqual([t["total"] for t in out["denenenler"]], [0, 0, 0])

    def test_a_shaped_query_is_sent_as_written(self):
        for q in ('"kira tespit"', "+kira -ceza", "kira AND tespit", "kira"):
            out, sent = self.run_search({q: 3}, query=q)
            self.assertEqual(sent, [q], q)
            self.assertNotIn("arama_modu", out)

    def test_a_hyphen_inside_a_word_is_not_an_operator(self):
        out, sent = self.run_search({'"e-imza sözleşmesi"': 4}, query="e-imza sözleşmesi")
        self.assertEqual(sent, ['"e-imza sözleşmesi"'])

    def test_modes(self):
        out, sent = self.run_search({}, query="kira tespit", kelime_modu="hepsi")
        self.assertEqual(sent, ["+kira +tespit", "kira tespit"])
        out, sent = self.run_search({}, query="kira tespit", kelime_modu="herhangi")
        self.assertEqual(sent, ["kira tespit"])
        out, sent = self.run_search({}, query="kira tespit", kelime_modu="yanlis")
        self.assertIn("error", out)
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
