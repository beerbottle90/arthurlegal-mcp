"""Dışa aktarım biçimleri ve okuma denetimleri — v0.3.0 incelemesinin bulguları (ağsız)."""

import json
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkgm_parsel as parseller  # noqa: E402
from tkgm_aktar import aktar  # noqa: E402
from tkgm_analiz import olc  # noqa: E402
from tkgm_koridor import csv_yaz  # noqa: E402


def _dosya(ozellik=None, halkalar=None):
    halkalar = halkalar or [[[29.0, 41.0], [29.001, 41.0], [29.001, 41.001], [29.0, 41.001], [29.0, 41.0]]]
    return json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature",
        "properties": dict({"AdaNo": "10", "ParselNo": "3"}, **(ozellik or {})),
        "geometry": {"type": "Polygon", "coordinates": halkalar}}]})


class Okuma(unittest.TestCase):
    def test_enlem_boylam_ters_sira_reddedilir(self):
        ters = [[[41.0, 29.0], [41.0, 29.001], [41.001, 29.001], [41.001, 29.0], [41.0, 29.0]]]
        with self.assertRaisesRegex(parseller.ParselHatasi, "enlem, boylam"):
            parseller.oku(_dosya(halkalar=ters))

    def test_tam_sayi_metrik_koordinat_ondaligi_sifir(self):
        h = [[[415000, 4540000], [415030, 4540000], [415030, 4540040], [415000, 4540040], [415000, 4540000]]]
        (p,) = parseller.oku(_dosya(halkalar=h), dom=30)
        self.assertEqual(p["kaynak"]["yazim"]["ondalik"] if "yazim" in p.get("kaynak", {}) else
                         parseller._yazim_hassasiyeti([h])["ondalik"], 0)

    def test_kml_tek_ic_sinirda_iki_delik(self):
        def lr(k):
            return "<LinearRing><coordinates>%s</coordinates></LinearRing>" % " ".join("%s,%s" % xy for xy in k + k[:1])
        dis = [(29.0, 41.0), (29.002, 41.0), (29.002, 41.002), (29.0, 41.002)]
        d1 = [(29.0002, 41.0002), (29.0004, 41.0002), (29.0004, 41.0004), (29.0002, 41.0004)]
        d2 = [(29.0012, 41.0012), (29.0014, 41.0012), (29.0014, 41.0014), (29.0012, 41.0014)]
        kml = ("<kml><Placemark><Polygon><outerBoundaryIs>%s</outerBoundaryIs><innerBoundaryIs>%s%s"
               "</innerBoundaryIs></Polygon></Placemark></kml>" % (lr(dis), lr(d1), lr(d2)))
        (p,) = parseller.oku(kml)
        self.assertEqual(len(p["cokgenler"][0]), 3)

    def test_oznitelik_temizlenir(self):
        (p,) = parseller.oku(_dosya({"Nitelik": "Tarla\x0b\ud800\nx" + "A" * 900}))
        n = p["oznitelik"]["nitelik"]
        self.assertNotIn("\x0b", n)
        self.assertNotIn("\n", n)
        self.assertLessEqual(len(n), 500)
        n.encode("utf-8")                        # eşleşmemiş vekil kalmadı


class Aktarim(unittest.TestCase):
    def setUp(self):
        (self.p,) = parseller.oku(_dosya({"Nitelik": "=HYPERLINK(\"x\");kötü\x0b", "Alan": "812,345"}))

    def test_kml_ve_svg_gecerli_xml(self):
        ET.fromstring(aktar(self.p, "kml").encode("utf-8"))

    def test_kml_alan_gidis_donus(self):
        (geri,) = parseller.oku(aktar(self.p, "kml"))
        self.assertAlmostEqual(geri["oznitelik"]["tapu_alani_m2"], 812.345, places=3)

    def test_geojson_sarim_rfc7946(self):
        cw = [[[29.0, 41.0], [29.0, 41.001], [29.001, 41.001], [29.001, 41.0], [29.0, 41.0]]]
        (p,) = parseller.oku(_dosya(halkalar=cw))
        h = json.loads(aktar(p, "geojson"))["features"][0]["geometry"]["coordinates"][0]
        alan2 = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(h, h[1:]))
        self.assertGreater(alan2, 0)             # dış halka saat yönünün tersine

    def test_csv_turkce_excel(self):
        c = aktar(self.p, "csv")
        self.assertTrue(c.startswith("﻿"))
        basliklar = c.splitlines()[0].lstrip("﻿").split(";")
        self.assertEqual(basliklar[-2:], ["dom", "epsg"])   # hangi dilimde olduğu dosyada yazar
        satir = c.splitlines()[1].split(";")
        self.assertEqual(len(satir), 9)
        self.assertIn(",", satir[3])              # ondalık virgül
        self.assertNotIn(".", satir[3])
        self.assertTrue(satir[-2].isdigit() and satir[-1].isdigit())

    def test_dxf_metni_tek_satir_ascii(self):
        (p,) = parseller.oku(_dosya({"AdaNo": "1\n0\nLINE", "ParselNo": "Ş"}))
        d = aktar(p, "dxf")
        self.assertNotIn("\nLINE\n", d)
        d.encode("ascii")

    def test_koridor_csv_formul_ve_tirnak(self):
        c = csv_yaz({"satirlar": [{"parsel": "=cmd|' /C calc'!A0", "nitelik": "a;b\nc", "parsel_alani_m2": 1.5,
                                   "kesisim_m2": 1.0, "oran_yuzde": 50.0, "eksen_m": 2.0, "eksen_geciyor": True,
                                   "ref": "r"}]})
        satir = c.splitlines()[1]
        self.assertTrue(satir.startswith("\"'="))  # formül etkisiz
        self.assertEqual(len(c.splitlines()), 2)   # satır sonu yeni satır uydurmadı
        self.assertIn('"a;b c"', satir)


class Xlsx(unittest.TestCase):
    def test_nan_hucre_uretmez(self):
        from tkgm_rapor import _hucre_xml
        self.assertEqual(_hucre_xml(1, 2, float("nan"), 0), "")
        self.assertEqual(_hucre_xml(1, 2, float("inf"), 0), "")


class Dem(unittest.TestCase):
    def test_yonlendirme_izlenmez(self):
        import tkgm_harita as harita
        with self.assertRaises(harita.HaritaHatasi):
            harita._YonlendirmeYok().redirect_request(None, None, 302, "Found", {}, "http://kotu.example/")


if __name__ == "__main__":
    unittest.main()
