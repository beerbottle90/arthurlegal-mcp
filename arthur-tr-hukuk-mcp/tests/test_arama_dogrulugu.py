"""Arama doğruluk testi (arama_dogrulugu.py): değerlendirme kuralları ve kalem dosyası. Ağsız.

    python -m unittest discover -s tests -p test_arama_dogrulugu.py -v      # arthur-tr-hukuk-mcp'den

Karşı deneme: 18.11.2026'da aynı sorgu sort=yeni ile çalıştırıldığında kalemdeki üç 8. HD kararının hiçbiri
ilk 10'da değildi. test_tarih_sirasi_kalir o yanıtın biçimini taşır: kalem bu hatayı yakalamalı.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import arama_dogrulugu as ad  # noqa: E402


def _karar(daire: str, esas: str) -> dict:
    return {"document_id": esas.replace("/", ""), "chamber": daire, "esas_no": esas, "karar_no": "x"}


KALEM = {
    "id": "ictihat-haciz-takas", "arac": "ictihat_ara", "argumanlar": {"query": "dosya alacağına haciz takas"},
    "ilk_n": 10, "en_az": 2,
    "beklenen": [{"chamber": "8. Hukuk Dairesi", "esas_no": "2014/14976"},
                 {"chamber": "8. Hukuk Dairesi", "esas_no": "2014/27629"},
                 {"chamber": "8. Hukuk Dairesi", "esas_no": "2017/10953"}],
}

# 18.11.2026 canlı ölçüm, ilgi sırası (ilk dört).
ILGI = {"results": [_karar("12. Hukuk Dairesi", "2016/25635"), _karar("8. Hukuk Dairesi", "2014/14976"),
                    _karar("8. Hukuk Dairesi", "2014/27629"), _karar("8. Hukuk Dairesi", "2017/10953")]}
# 18.11.2026 canlı ölçüm, sort=yeni: ilk 10 karar 2026 tarihli, beklenenlerin hiçbiri yok.
TARIH = {"results": [_karar("11. Hukuk Dairesi", "2026/1597"), _karar("1. Hukuk Dairesi", "2025/4822"),
                     _karar("11. Ceza Dairesi", "2021/32758"), _karar("Hukuk Genel Kurulu", "2025/651"),
                     _karar("5. Hukuk Dairesi", "2025/16175"), _karar("11. Hukuk Dairesi", "2025/5749"),
                     _karar("3. Hukuk Dairesi", "2025/5351"), _karar("3. Hukuk Dairesi", "2026/1510"),
                     _karar("3. Hukuk Dairesi", "2025/5556"), _karar("11. Hukuk Dairesi", "2025/5491")]}


class Degerlendir(unittest.TestCase):
    def test_ilgi_sirasi_gecer(self):
        s = ad.degerlendir(KALEM, ILGI)
        self.assertEqual(s["sinif"], "GECTI")
        self.assertEqual([x["sira"] for x in s["siralar"]], [2, 3, 4])

    def test_tarih_sirasi_kalir(self):
        s = ad.degerlendir(KALEM, TARIH)
        self.assertEqual((s["sinif"], s["bulunan"]), ("KALDI", 0))
        self.assertIn("0/3", s["ayrinti"])

    def test_ilk_n_disinda_kalan_sayilmaz(self):
        kalem = dict(KALEM, ilk_n=2)
        self.assertEqual(ad.degerlendir(kalem, ILGI)["sinif"], "KALDI")

    def test_bos_liste_kalir_erisim_sayilmaz(self):
        # "Unavailable ≠ empty": cevap gelmiş ama boşsa sıralama bozuk sayılır.
        self.assertEqual(ad.degerlendir(KALEM, {"results": []})["sinif"], "KALDI")

    def test_buyuk_harf_ve_bosluk_farki(self):
        sonuc = {"results": [_karar("8.  HUKUK DAİRESİ", "2014/14976"), _karar("8. hukuk dairesi", "2014/27629")]}
        self.assertEqual(ad.degerlendir(KALEM, sonuc)["sinif"], "GECTI")

    def test_erisim_ve_hata(self):
        self.assertEqual(ad.degerlendir(KALEM, {"error": "HTTP 503 Service Unavailable"})["sinif"], "ERISIM")
        self.assertEqual(ad.degerlendir(KALEM, {"error": "x", "unavailable": True})["sinif"], "ERISIM")
        self.assertEqual(ad.degerlendir(KALEM, {"error": "Unknown tool: ictihat_ara"})["sinif"], "HATA")
        self.assertEqual(ad.degerlendir(KALEM, {"_metin": "düz metin"})["sinif"], "HATA")

    def test_madde_kalemi(self):
        kalem = {"id": "m", "arac": "mevzuat_icinde_ara", "argumanlar": {}, "ilk_n": 3, "en_az": 1,
                 "beklenen": [{"madde_no": "120"}]}
        self.assertEqual(ad.degerlendir(kalem, {"results": [{"madde_no": "120"}]})["sinif"], "GECTI")
        dort = {"results": [{"madde_no": n} for n in ("115", "117", "36", "120")]}
        self.assertEqual(ad.degerlendir(kalem, dort)["sinif"], "KALDI")


class Cikis(unittest.TestCase):
    def test_kodlar(self):
        self.assertEqual(ad.cikis_kodu([{"sinif": "GECTI"}]), 0)
        self.assertEqual(ad.cikis_kodu([{"sinif": "GECTI"}, {"sinif": "ERISIM"}]), 2)
        self.assertEqual(ad.cikis_kodu([{"sinif": "ERISIM"}, {"sinif": "KALDI"}]), 1)
        self.assertEqual(ad.cikis_kodu([{"sinif": "HATA"}]), 1)

    def test_issue_govdesi(self):
        metin = ad.issue_govdesi(ad.degerlendir(KALEM, TARIH), KALEM, "https://ornek/kosu")
        self.assertIn("ictihat-haciz-takas", metin)
        self.assertIn("ilk 10 sonuçta yok", metin)
        self.assertIn("https://ornek/kosu", metin)


class KalemDosyasi(unittest.TestCase):
    def test_kalemler_tutarli(self):
        with open(ad.KALEMLER, encoding="utf-8") as f:
            kalemler = json.load(f)["kalemler"]
        self.assertGreaterEqual(len(kalemler), 6)
        idler = [k["id"] for k in kalemler]
        self.assertEqual(len(idler), len(set(idler)))
        for k in kalemler:
            with self.subTest(k["id"]):
                self.assertIn(k["arac"], ("ictihat_ara", "mevzuat_icinde_ara", "aym_ara", "uyusmazlik_ara",
                                          "kurum_karari_ara"))
                self.assertTrue(k["beklenen"] and all(isinstance(b, dict) and b for b in k["beklenen"]))
                self.assertTrue(1 <= k["en_az"] <= len(k["beklenen"]))
                self.assertGreaterEqual(k["ilk_n"], 1)
                # Beklenen kayıt ölçülmeden yazılmaz.
                self.assertTrue(k.get("olcum") and k.get("kaynak"))


if __name__ == "__main__":
    unittest.main()
