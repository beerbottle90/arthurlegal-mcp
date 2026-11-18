"""Signs Bedesten refuses are converted before the request -- offline.

Run: python -m unittest tests.test_ictihat_isaret

Bedesten answers "Sadece harf ve rakam içeren aramalar yapılabilir" to any query
holding a sign outside letters, digits, spaces and " + - ( ) ! & |. Measured
2026-11-19 (Yargıtay + Danıştay), one request per sign: refused . / , ' ’ : ; ?
* % § _ – “ ” and letters such as â and é. The index splits words on those
signs, so a space finds the same text: the phrase "01 04 1974" -> 193
decisions, "+muris +muvazaası +01 +04 +1974" -> 169. A date or an İBK number
typed the way it is cited therefore used to fail outright.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sources import bedesten_ictihat  # noqa: E402
from sources.bedesten_ictihat import _temizle  # noqa: E402

# Everything the live validator accepted on 2026-11-19 (letters, digits, space, signs).
KABUL = set("abcçdefgğhıijklmnoöprsştuüvyzqwxABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZQWX0123456789 \"+-()!&|")


class ValidatingBedesten:
    """Refuses a phrase the way Bedesten does; records every phrase it was sent."""

    def __init__(self, total: int = 7):
        self.total, self.sent = total, []

    def post_json(self, path, payload):
        phrase = payload["data"]["phrase"]
        self.sent.append(phrase)
        if set(phrase) - KABUL:
            return {"metadata": {"FMTY": "ERROR", "FMTE": "Parameter validation exception:Lütfen Arama Kriterlerini "
                                 "Kontrol Ediniz! Sadece harf ve rakam içeren aramalar yapılabilir."}}
        rows = [{"documentId": "1", "itemType": {"name": "YARGITAYKARARI", "description": "Yargıtay Kararı"},
                 "birimAdi": "1. Hukuk Dairesi", "esasNo": "2013/1", "kararNo": "2013/2",
                 "kararTarihiStr": "01.01.2013"}]
        return {"metadata": {"FMTY": "SUCCESS"}, "data": {"total": self.total, "emsalKararList": rows}}


def ara(**args):
    fake = ValidatingBedesten()
    saved = bedesten_ictihat._http
    bedesten_ictihat._http = fake
    try:
        return bedesten_ictihat.search(args), fake.sent
    finally:
        bedesten_ictihat._http = saved


class TemizleTest(unittest.TestCase):
    def test_dates_and_ibk_numbers_become_spaced_words(self):
        temiz, degisen = _temizle("muris muvazaası 01.04.1974 1/2 içtihadı birleştirme")
        self.assertEqual(temiz, "muris muvazaası 01 04 1974 1 2 içtihadı birleştirme")
        self.assertEqual(degisen, [(".", " "), ("/", " ")])

    def test_docket_number_keeps_the_0_8_0_behaviour(self):
        self.assertEqual(_temizle("2014/27629")[0], "2014 27629")

    def test_apostrophes_commas_and_other_refused_signs(self):
        self.assertEqual(_temizle("TBK'nın 344. maddesi")[0], "TBK nın 344 maddesi")
        self.assertEqual(_temizle("Yargıtay’ın kararı; kira, tespit?")[0], "Yargıtay ın kararı kira tespit")
        self.assertEqual(_temizle("HMK m:389 % 5 § 2 * _ –")[0], "HMK m 389 5 2")

    def test_letters_outside_the_alphabet_lose_their_accent(self):
        temiz, degisen = _temizle("yapay zekâ hâkim kâr café")
        self.assertEqual(temiz, "yapay zeka hakim kar cafe")
        self.assertIn(("â", "a"), degisen)

    def test_turkish_letters_are_untouched(self):
        metin = "çığöşü ÇĞİÖŞÜ ıi Iİ"
        self.assertEqual(_temizle(metin), (metin, []))

    def test_word_quotes_become_a_phrase(self):
        self.assertEqual(_temizle("“muris muvazaası” tapu")[0], '"muris muvazaası" tapu')

    def test_a_loose_combining_dot_is_dropped(self):
        # "İ".lower() in Python is "i" + U+0307; pasted text sometimes carries it.
        self.assertEqual(_temizle("İSTİNAF".lower())[0], "istinaf")

    def test_accepted_query_signs_are_left_as_written(self):
        for q in ('+"takas mahsup" -ceza', "e-imza", "(muris)", "kira AND tespit", "kira  tespit"):
            self.assertEqual(_temizle(q), (q, []), q)


class SearchTest(unittest.TestCase):
    def test_the_reported_query_now_reaches_bedesten(self):
        out, sent = ara(query="muris muvazaası 01.04.1974 1/2 içtihadı birleştirme")
        self.assertNotIn("error", out)
        self.assertEqual(sent, ['"muris muvazaası 01 04 1974 1 2 içtihadı birleştirme"'])
        self.assertEqual(out["uygulanan_sorgu"], sent[-1])
        self.assertEqual(out["query"], "muris muvazaası 01 04 1974 1 2 içtihadı birleştirme")
        self.assertIn('"." -> boşluk', out["sorgu_notu"])
        self.assertIn('"/" -> boşluk', out["sorgu_notu"])

    def test_every_sent_phrase_passes_the_validator(self):
        for q in ("TBK'nın 344. maddesi", "yapay zekâ, fesih", "HGK 2014/27629: takas",
                  "“kira tespit” 01.01.2020", "1/2 sayılı İBK"):
            for mode in ("ifade", "hepsi", "herhangi"):
                out, sent = ara(query=q, kelime_modu=mode)
                self.assertNotIn("error", out, (q, mode))
                for phrase in sent:
                    self.assertFalse(set(phrase) - KABUL, (q, mode, phrase))

    def test_a_cited_date_is_searched_as_a_phrase(self):
        # One token typed, three words after conversion: the ladder's phrase rung keeps them together.
        out, sent = ara(query="01.04.1974")
        self.assertEqual(sent, ['"01 04 1974"'])
        self.assertEqual(out["arama_modu"], "tam ifade")

    def test_a_single_word_shows_the_applied_query_too(self):
        out, sent = ara(query="zekâ")
        self.assertEqual(sent, ["zeka"])
        self.assertEqual(out["uygulanan_sorgu"], "zeka")
        self.assertNotIn("arama_modu", out)

    def test_clean_query_carries_no_sign_note(self):
        out, _ = ara(query="kira tespit")
        self.assertNotIn("sorgu_notu", out)
        out, _ = ara(query="+kira -ceza")
        self.assertNotIn("sorgu_notu", out)
        self.assertNotIn("uygulanan_sorgu", out)

    def test_a_query_of_signs_only_is_refused_before_any_request(self):
        out, sent = ara(query="./?")
        self.assertIn("error", out)
        self.assertEqual(sent, [])

    def test_schema_tells_the_caller_signs_are_converted(self):
        aciklama = bedesten_ictihat.SEARCH_SCHEMA["properties"]["query"]["description"]
        self.assertIn("01.04.1974 → 01 04 1974", aciklama)
        self.assertIn("uygulanan_sorgu", aciklama)


if __name__ == "__main__":
    unittest.main()
