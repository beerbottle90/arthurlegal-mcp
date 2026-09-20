"""Koridor kesişimi ve tapu kaydı ayrıştırma testleri (ağsız).

Koridor sonuçları kapalı formüle karşı sınanır: dikdörtgen parselden geçen düz
koridor = boy × genişlik; parsel içinde biten hat = dikdörtgen + yarım daire.
"""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402
import tkgm_geo as geo  # noqa: E402
import tkgm_koridor as koridor  # noqa: E402
import tkgm_parsel as parseller  # noqa: E402
import tkgm_tapu as tapu  # noqa: E402
from mcpcore import McpError  # noqa: E402
from test_offline import ornek_geojson  # noqa: E402

_C, _S = math.cos(math.radians(20.0)), math.sin(math.radians(20.0))


def yerel(dx, dy):
    """ornek_geojson'un 40×30 m dikdörtgeninin yerel eksenlerinden (boylam, enlem)."""
    enlem, boylam = geo.tm_geri(520000.0 + dx * _C - dy * _S, 4420000.0 + dx * _S + dy * _C, 33)
    return (boylam, enlem)


class Koridor(unittest.TestCase):
    def setUp(self):
        (self.p,) = parseller.oku(ornek_geojson())

    def _tek(self, hat, genislik):
        sonuc = koridor.kesisim([self.p], [hat], genislik)
        return sonuc, (sonuc["satirlar"][0] if sonuc["satirlar"] else None)

    def test_boydan_boya_gecen_koridor(self):
        sonuc, s = self._tek([yerel(-50, 15), yerel(90, 15)], 10.0)
        self.assertAlmostEqual(s["kesisim_m2"], 400.0, delta=0.2)
        self.assertAlmostEqual(s["eksen_m"], 40.0, delta=0.01)
        self.assertAlmostEqual(s["oran_yuzde"], 33.33, delta=0.05)
        self.assertTrue(s["eksen_geciyor"])
        self.assertLess(sonuc["sayisal_hata_yuzde_en_kotu"], 0.05)
        self.assertAlmostEqual(sonuc["hat_boyu_m"], 140.0, delta=0.01)

    def test_parsel_icinde_biten_hat_yarim_daire_ekler(self):
        _, s = self._tek([yerel(-50, 15), yerel(20, 15)], 10.0)
        self.assertAlmostEqual(s["kesisim_m2"], 200.0 + math.pi * 25.0 / 2.0, delta=0.2)
        self.assertAlmostEqual(s["eksen_m"], 20.0, delta=0.01)

    def test_yalniz_tampon_dokunur(self):
        _, s = self._tek([yerel(-50, -3), yerel(90, -3)], 10.0)     # eksen parselin 3 m dışında
        self.assertAlmostEqual(s["kesisim_m2"], 80.0, delta=0.2)
        self.assertFalse(s["eksen_geciyor"])
        self.assertEqual(s["eksen_m"], 0.0)

    def test_kirik_hat_koseyi_iki_kez_saymaz(self):
        """Dirsekte iki kapsül üst üste biner; birleşim alınmazsa alan şişer."""
        _, s = self._tek([yerel(-50, 15), yerel(20, 15), yerel(20, 80)], 10.0)
        beklenen = 25.0 * 10.0 + 10.0 * 20.0 - 0.0    # yatay kol 0-25, düşey kol 15-30 (x 15-25)
        # yatay kol x∈[0,25] (dirsek kapsülüyle), düşey kol y∈[15,30]: 250 + 10×(30-20) + köşe yayı
        ust = 250.0 + 100.0 + 25.0
        self.assertGreater(s["kesisim_m2"], 300.0)
        self.assertLess(s["kesisim_m2"], ust)
        self.assertAlmostEqual(s["eksen_m"], 20.0 + 15.0, delta=0.01)
        self.assertIsNotNone(beklenen)

    def test_uzaktaki_hat_satir_uretmez(self):
        sonuc, s = self._tek([yerel(500, 500), yerel(900, 500)], 20.0)
        self.assertIsNone(s)
        self.assertEqual(sonuc["etkilenen_parsel"], 0)

    def test_hat_oku_geojson_ve_kml(self):
        a, b = yerel(-50, 15), yerel(90, 15)
        gj = json.dumps({"type": "Feature", "geometry": {"type": "LineString",
                                                         "coordinates": [list(a), list(b)]}})
        kml = ("<kml xmlns='http://www.opengis.net/kml/2.2'><Placemark><LineString><coordinates>"
               "%f,%f,0 %f,%f,0</coordinates></LineString></Placemark></kml>" % (a + b))
        for metin in (gj, kml):
            (hat,) = koridor.hat_oku(metin)
            self.assertEqual(len(hat), 2)
            self.assertAlmostEqual(hat[0][0], a[0], places=5)

    def test_ters_sira_yakalanir(self):
        with self.assertRaises(koridor.KoridorHatasi):
            koridor.kesisim([self.p], [[(39.9, 32.8), (39.91, 32.81)]], 10.0)   # (enlem, boylam) verilmiş


class KoridorAraci(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TKGM_CIKTI"] = self.tmp
        os.environ["TKGM_DOSYA_ERISIMI"] = "1"
        out = json.loads(server._t_parsel_oku({"icerik": ornek_geojson()}))
        self.ref = out["parseller"][0]["ref"]

    def tearDown(self):
        os.environ.pop("TKGM_CIKTI", None)
        os.environ.pop("TKGM_DOSYA_ERISIMI", None)

    def test_arac_enlem_boylam_sirasi_ve_csv(self):
        a, b = yerel(-50, 15), yerel(90, 15)
        out = json.loads(server._t_koridor({"refs": [self.ref], "genislik_m": 10,
                                            "hat": [[a[1], a[0]], [b[1], b[0]]]}))
        self.assertEqual(out["etkilenen_parsel"], 1)
        self.assertIn("400,", out["satirlar"][0])
        with open(out["dosya"], encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().strip().splitlines()), 2)

    def test_hat_eksikse_hata(self):
        with self.assertRaises(McpError):
            server._t_koridor({"refs": [self.ref], "genislik_m": 10})


ORNEK_TAPU = """TAPU KAYIT BİLGİLERİ
İl: Ankara
İlçe: Çankaya
Mahalle/Köy: Örnek
Ada: 123
Parsel: 4
Yüzölçümü: 1.195,00 m2
Ana Taşınmaz Nitelik: Tarla
Malik: AHMET ÖRNEK (T.C. 12345678901)
Hisse Pay/Payda: 1/2
Edinme Sebebi: Satış
Malik: AYŞE ÖRNEK
Hisse Pay/Payda: 1/2
ŞERHLER
Aile konutu şerhi - 12.03.2019 / 4521
BEYANLAR
2/B alanında kalmaktadır
REHİNLER
X Bankası A.Ş. lehine 1. derece 2.000.000 TL ipotek
"""


class Tapu(unittest.TestCase):
    def test_sema_maskeleme_ve_isaretler(self):
        k = tapu.ayristir(ORNEK_TAPU)
        self.assertEqual(k["tasinmaz"]["ada"], "123")
        self.assertEqual(k["tasinmaz"]["yuzolcumu_m2"], 1195.0)
        self.assertEqual(len(k["malikler"]), 2)
        self.assertEqual(k["malikler"][0]["hisse"], "1/2")
        self.assertNotIn("12345678901", json.dumps(k, ensure_ascii=False))
        self.assertIn("*********01", k["malikler"][0]["ad"])
        self.assertEqual(len(k["serhler"]), 1)
        self.assertTrue(any("TMK 194" in i for i in k["isaretler"]))
        self.assertTrue(any("6292" in i for i in k["isaretler"]))
        self.assertTrue(any("İpotek" in i for i in k["isaretler"]))
        self.assertTrue(k["guven"].startswith("düşük"))

    def test_capraz_kontrol_alan_farkini_soyler(self):
        (p,) = parseller.oku(ornek_geojson())
        notlar = tapu.capraz_kontrol(tapu.ayristir(ORNEK_TAPU), p, 1200.0)
        self.assertTrue(any("1195.00" in n and "+5.00" in n for n in notlar))
        self.assertFalse(any("uyuşmuyor" in n for n in notlar))

    def test_paylasilan_sunucuda_calismaz(self):
        os.environ["TKGM_DOSYA_ERISIMI"] = "0"
        try:
            with self.assertRaises(McpError):
                server._t_tapu({"metin": ORNEK_TAPU})
        finally:
            os.environ.pop("TKGM_DOSYA_ERISIMI", None)


if __name__ == "__main__":
    unittest.main()
