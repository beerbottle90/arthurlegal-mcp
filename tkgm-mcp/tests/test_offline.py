"""Ağsız testler: python -m unittest discover -s tests

İzdüşüm, ezberlenmiş bir koordinata karşı değil, bağımsız hesaplara karşı
sınanır: meridyen yayı sayısal integralle, konformluk ve ölçek katsayısı sonlu
farkla. Yanlış hatırlanmış bir referans değeri, doğru formülü "bozuk" gösterir.
"""

import json
import math
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
import tkgm_geo as geo  # noqa: E402
import tkgm_hukuk as hukuk  # noqa: E402
import tkgm_parsel as parseller  # noqa: E402
from mcpcore import McpError  # noqa: E402
from tkgm_aktar import aktar  # noqa: E402
from tkgm_analiz import olc, tr_bicim  # noqa: E402

_E2 = geo._F * (2.0 - geo._F)


def _m(fi):   # meridyen eğrilik yarıçapı
    return geo._A * (1 - _E2) / (1 - _E2 * math.sin(fi) ** 2) ** 1.5


def _n(fi):   # meridyene dik doğrultuda eğrilik yarıçapı
    return geo._A / math.sqrt(1 - _E2 * math.sin(fi) ** 2)


def ornek_geojson(genislik=40.0, derinlik=30.0, donme=20.0, alan_yazisi="1.200,00"):
    """TM33'te kurulmuş, döndürülmüş bir dikdörtgen; Parsel Sorgu öznitelik adlarıyla."""
    c, s = math.cos(math.radians(donme)), math.sin(math.radians(donme))
    koseler = [(0, 0), (genislik, 0), (genislik, derinlik), (0, derinlik)]
    halka = []
    for dx, dy in koseler:
        y, x = 520000.0 + dx * c - dy * s, 4420000.0 + dx * s + dy * c
        enlem, boylam = geo.tm_geri(y, x, 33)
        halka.append([boylam, enlem])
    halka.append(halka[0])
    return json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [halka]},
        "properties": {"ilAd": "Ankara", "ilceAd": "Çankaya", "mahalleAd": "Örnek", "adaNo": "123",
                       "parselNo": "4", "alan": alan_yazisi, "nitelik": "Tarla", "pafta": "I29"}}]})


class Izdusum(unittest.TestCase):
    def test_meridyen_yayi_sayisal_integrale_esit(self):
        fi = math.radians(40.0)
        adim = 20000
        h = fi / adim
        yay = sum((_m(i * h) + 4 * _m((i + 0.5) * h) + _m((i + 1) * h)) * h / 6 for i in range(adim))
        saga, yukari = geo.tm_ileri(40.0, 33.0, 33)
        self.assertAlmostEqual(saga, 500000.0, places=6)
        self.assertAlmostEqual(yukari, yay, delta=0.001)

    def test_gidis_donus(self):
        for enlem, boylam, dom in ((36.2, 28.4, 27), (39.93, 32.85, 33), (41.9, 44.6, 45)):
            y, x = geo.tm_ileri(enlem, boylam, dom)
            e2, b2 = geo.tm_geri(y, x, dom)
            self.assertAlmostEqual(enlem, e2, places=9)
            self.assertAlmostEqual(boylam, b2, places=9)

    def test_konform_ve_olcek_katsayisi(self):
        enlem, boylam, dom, d = 39.5, 34.2, 33, 1e-6
        y, x = geo.tm_ileri(enlem, boylam, dom)
        yk, xk = geo.tm_ileri(enlem + math.degrees(d), boylam, dom)
        yd, xd = geo.tm_ileri(enlem, boylam + math.degrees(d), dom)
        fi = math.radians(enlem)
        k_kuzey = math.hypot(yk - y, xk - x) / (_m(fi) * d)
        k_dogu = math.hypot(yd - y, xd - x) / (_n(fi) * math.cos(fi) * d)
        self.assertAlmostEqual(k_kuzey, k_dogu, places=6)
        ic_carpim = (yk - y) * (yd - y) + (xk - x) * (xd - x)
        self.assertAlmostEqual(ic_carpim / (math.hypot(yk - y, xk - x) * math.hypot(yd - y, xd - x)),
                               0.0, places=6)
        beklenen = 1.0 + (y - 500000.0) ** 2 / (2.0 * _m(fi) * _n(fi))
        self.assertAlmostEqual(k_kuzey, beklenen, places=6)

    def test_dilim_secimi(self):
        self.assertEqual(geo.dom_sec(32.85), 33)
        self.assertEqual(geo.dom_sec(28.4), 27)
        self.assertEqual(geo.dom_sec(44.9), 45)
        self.assertEqual(geo.dom_sec(32.85, "utm6"), 33)
        self.assertEqual(geo.dom_sec(29.0, "utm6"), 27)


class Okuma(unittest.TestCase):
    def test_tr_sayi(self):
        self.assertEqual(parseller.tr_sayi("1.312,40"), 1312.40)
        self.assertEqual(parseller.tr_sayi("1312.4"), 1312.4)
        self.assertEqual(parseller.tr_sayi("12.500"), 12500.0)
        self.assertEqual(parseller.tr_sayi("1,312.40"), 1312.40)
        self.assertEqual(parseller.tr_sayi("850,5"), 850.5)
        self.assertIsNone(parseller.tr_sayi(""))

    def test_geojson_alan_ve_oznitelik(self):
        (p,) = parseller.oku(ornek_geojson())
        self.assertEqual(p["oznitelik"]["ada"], "123")
        self.assertEqual(p["oznitelik"]["ilce"], "Çankaya")
        self.assertEqual(p["oznitelik"]["tapu_alani_m2"], 1200.0)
        olcu = olc(p)
        self.assertEqual(olcu["dom"], 33)
        self.assertAlmostEqual(olcu["alan_m2"], 1200.0, delta=0.01)
        self.assertAlmostEqual(olcu["cevre_m"], 140.0, delta=0.01)
        self.assertEqual(len(p["cokgenler"][0][0]), 4)   # kapanış noktası atıldı

    def test_kml_gidis_donus(self):
        (p,) = parseller.oku(ornek_geojson())
        (q,) = parseller.oku(aktar(p, "kml"))
        self.assertEqual(q["oznitelik"]["parsel"], "4")
        self.assertEqual(q["oznitelik"]["tapu_alani_m2"], 1200.0)
        self.assertAlmostEqual(olc(q)["alan_m2"], 1200.0, delta=0.02)

    def test_projeksiyon_koordinatli_dosya_dom_ister(self):
        gj = json.dumps({"type": "Polygon", "coordinates": [[
            [520000, 4420000], [520040, 4420000], [520040, 4420030], [520000, 4420030]]]})
        with self.assertRaises(parseller.ParselHatasi):
            parseller.oku(gj)
        (p,) = parseller.oku(gj, dom=33)
        self.assertAlmostEqual(olc(p)["alan_m2"], 1200.0, delta=0.01)

    def test_doctype_reddedilir(self):
        with self.assertRaises(parseller.ParselHatasi):
            parseller.oku('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><kml/>')

    def test_ic_halka_alandan_duser(self):
        gj = json.loads(ornek_geojson())
        dis = gj["features"][0]["geometry"]["coordinates"][0]
        mb = sum(p[0] for p in dis[:4]) / 4
        me = sum(p[1] for p in dis[:4]) / 4
        ic = [[mb + (p[0] - mb) * 0.5, me + (p[1] - me) * 0.5] for p in dis]
        gj["features"][0]["geometry"]["coordinates"].append(ic)
        (p,) = parseller.oku(json.dumps(gj))
        self.assertAlmostEqual(olc(p)["alan_m2"], 900.0, delta=0.02)


class Araclar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TKGM_CIKTI"] = self.tmp
        os.environ["TKGM_DOSYA_ERISIMI"] = "1"
        self.dosya = os.path.join(self.tmp, "parsel.geojson")
        with open(self.dosya, "w", encoding="utf-8") as fh:
            fh.write(ornek_geojson())

    def tearDown(self):
        os.environ.pop("TKGM_CIKTI", None)
        os.environ.pop("TKGM_DOSYA_ERISIMI", None)

    def _oku(self):
        out = json.loads(server._t_parsel_oku({"dosya": self.dosya}))
        return out["parseller"][0]

    def test_oku_rejim_uyarisi_verir(self):
        kayit = self._oku()
        self.assertEqual(kayit["parsel"], "Ankara/Çankaya/Örnek 123/4")
        self.assertTrue(any("5403" in r for r in kayit["rejim"]))

    def test_kroki_dosyaya_yazilir_ve_gecerli_svg(self):
        ref = self._oku()["ref"]
        out = json.loads(server._t_kroki({"ref": ref, "koordinat_tablosu": True}))
        self.assertNotIn("svg", out)                       # içerik yanıta gömülmez
        self.assertEqual(out["olcek"], "1/500")
        self.assertEqual(out["etiketlenen_kenar"], 4)
        kok = ET.parse(out["dosya"]).getroot()
        metinler = [t.text for t in kok.iter("{http://www.w3.org/2000/svg}text")]
        self.assertIn("123/4", metinler)
        self.assertIn("40,00", metinler)
        self.assertIn("30,00", metinler)
        self.assertLess(out["boyut_kb"], 12)

    def test_olcekli_cizim(self):
        """1/500'de 40 m'lik kenar kâğıtta 80 mm olmalı."""
        ref = self._oku()["ref"]
        svg = json.loads(server._t_kroki({"ref": ref, "inline": True}))["svg"]
        kok = ET.fromstring(svg)
        yol = [p for p in kok.iter("{http://www.w3.org/2000/svg}path") if "#fff3d6" in p.get("fill", "")][0]
        nokta = [tuple(map(float, t.strip("ML ").split(","))) for t in yol.get("d").rstrip(" Z").split(" L")]
        self.assertAlmostEqual(math.dist(nokta[0], nokta[1]), 80.0, delta=0.05)

    def test_paylasilan_kipte_dosya_yok(self):
        ref = self._oku()["ref"]
        os.environ["TKGM_DOSYA_ERISIMI"] = "0"
        with self.assertRaises(McpError):
            server._t_parsel_oku({"dosya": self.dosya})
        out = json.loads(server._t_kroki({"ref": ref}))
        self.assertIn("<svg", out["svg"])
        self.assertNotIn("dosya", out)
        self.assertIsNone(server._t_status({})["cikti_klasoru"])

    def test_uzanti_siniri(self):
        with self.assertRaises(McpError):
            server._t_parsel_oku({"dosya": os.path.join(self.tmp, "..", "gizli.txt")})

    def test_geometri_kenar_ve_aci(self):
        ref = self._oku()["ref"]
        out = json.loads(server._t_geometri({"ref": ref}))
        self.assertEqual(len(out["kenarlar"]), 4)
        self.assertTrue(out["kenarlar"][0].startswith("1-2 40,00 m"))
        self.assertEqual(out["ic_acilar_g"].split()[0], "1:100,00")
        self.assertNotIn("tm", out)

    def test_disa_aktar_bicimler(self):
        ref = self._oku()["ref"]
        for bicim in ("geojson", "kml", "dxf", "csv"):
            out = json.loads(server._t_disa_aktar({"ref": ref, "bicim": bicim}))
            self.assertTrue(os.path.getsize(out["dosya"]) > 0)
        dxf = json.loads(server._t_disa_aktar({"ref": ref, "bicim": "dxf", "inline": True}))["icerik"]
        self.assertEqual(dxf.count("VERTEX"), 4)
        self.assertTrue(dxf.endswith("EOF\n"))

    def test_koordinat_donusumu_gidis_donus(self):
        ileri = json.loads(server._t_koordinat({"yon": "cografi_tm", "noktalar": [[39.93, 32.85]]}))
        self.assertIn("DOM 33", ileri["sistem"])
        geri = json.loads(server._t_koordinat({"yon": "tm_cografi", "dom": 33,
                                               "noktalar": ileri["noktalar"]}))
        self.assertAlmostEqual(geri["noktalar"][0][0], 39.93, places=7)
        with self.assertRaises(McpError):
            server._t_koordinat({"yon": "tm_cografi", "noktalar": [[500000, 4420000]]})

    def test_baglanti_tkgm_ye_istek_atmaz_yalniz_url_kurar(self):
        out = json.loads(server._t_baglanti({"enlem": 39.93, "boylam": 32.85}))
        self.assertEqual(out["parsel_sorgu"], "https://parselsorgu.tkgm.gov.tr/#ara/cografi/39.930000/32.850000")
        with self.assertRaises(McpError):
            server._t_baglanti({"enlem": 32.85, "boylam": 39.93})
        adimli = json.loads(server._t_baglanti({"il": "Ankara", "ada": "123", "parsel": "4"}))
        self.assertEqual(len(adimli["adimlar"]), 5)

    def test_bilinmeyen_ref(self):
        with self.assertRaises(McpError):
            server._t_geometri({"ref": "yok"})


class Hukuk(unittest.TestCase):
    def test_her_konu_dayanak_ve_cagri_tasir(self):
        for konu in hukuk.KONULAR:
            k = hukuk.kopru(konu)
            self.assertTrue(k["dayanak"], konu)
            self.assertEqual(len(k["mevzuat"]), len(k["dayanak"]))
            for cagri in k["mevzuat"] + k["ictihat"]:
                self.assertIn(cagri["arac"], ("tr_mevzuat_ara", "tr_ictihat_ara"))

    def test_mulga_sinirdas_onalimi_yururlukte_gosterilmez(self):
        """5403 m. 8/İ'nin sınırdaş fıkrası 7255/20 ile kalktı; köprü bunu uyarı olarak taşımalı."""
        k = hukuk.kopru("onalim")
        self.assertIn("KALDIRILDI", k["uyari"])
        self.assertNotIn("sınırdaş", " ".join(d["maddeler"] for d in k["dayanak"]))
        self.assertTrue(k["teyit"].startswith("2026-"))
        self.assertTrue(hukuk.kopru("gecit_hakki")["teyit"].startswith("YOK"))

    def test_nitelik_tam_sozcuk_esler(self):
        self.assertTrue(hukuk.nitelik_rejimleri("Bağ"))
        self.assertTrue(hukuk.nitelik_rejimleri("Kargir ev ve bahçesi"))
        self.assertTrue(hukuk.nitelik_rejimleri("Sit alanı"))
        self.assertFalse(hukuk.nitelik_rejimleri("Bağımsız bölüm"))
        self.assertFalse(hukuk.nitelik_rejimleri("Site yönetim binası"))

    def test_argumansiz_konu_listesi(self):
        out = json.loads(server._t_hukuk({}))
        self.assertEqual(len(out["konular"]), len(hukuk.KONULAR))


class Bicim(unittest.TestCase):
    def test_tr_bicim(self):
        self.assertEqual(tr_bicim(1312.4), "1.312,40")
        self.assertEqual(tr_bicim(500, 0), "500")
        self.assertEqual(tr_bicim(-2.4), "-2,40")


if __name__ == "__main__":
    unittest.main()
