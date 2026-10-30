"""Harç hesabı ve koordinat hassasiyeti testleri (ağsız).

Harç testleri beklenen tutarı veriden türetir, sabit yazmaz: tarife değişince test
değil veri değişmeli. Sınanan şey aritmetik ve kurallardır (matrah tabanı, iki
yükümlü, katsayısız hesap yapmama).
"""

import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402
import tkgm_geo as geo  # noqa: E402
import tkgm_harc as harc  # noqa: E402
import tkgm_parsel as parseller  # noqa: E402
from mcpcore import McpError  # noqa: E402
from tkgm_analiz import olc  # noqa: E402
from tkgm_kroki import ciz  # noqa: E402


def parsel_sorgu_bicimi(ondalik=5):
    """Gerçek Parsel Sorgu dışa aktarımının biçimi: büyük harfli anahtarlar, 5 ondalık, crs alanı.
    Geometri sentetiktir (60×25 m); gerçek parsel verisi depoya konmaz."""
    halka = []
    for dx, dy in ((0, 0), (60, 0), (60, 25), (0, 25)):
        enlem, boylam = geo.tm_geri(415000.0 + dx, 4545000.0 + dy, 30)
        halka.append([round(boylam, ondalik), round(enlem, ondalik)])
    halka.append(halka[0])
    return json.dumps({"features": [{"type": "Feature",
                                     "geometry": {"type": "Polygon", "coordinates": [halka]},
                                     "properties": {"ParselNo": "7", "Alan": "1.500,00", "Mevkii": "",
                                                    "Nitelik": "Kargir Apartman", "Ada": "100",
                                                    "Il": "İstanbul", "Ilce": "Örnekilçe", "Pafta": "69/71",
                                                    "Mahalle": "Örnek"}}],
                       "type": "FeatureCollection",
                       "crs": {"type": "name", "properties": {"name": "EPSG:4326"}}})


class Hassasiyet(unittest.TestCase):
    def test_gercek_bicimin_anahtarlari_okunur(self):
        (p,) = parseller.oku(parsel_sorgu_bicimi())
        oz = p["oznitelik"]
        self.assertEqual((oz["il"], oz["ilce"], oz["mahalle"], oz["ada"], oz["parsel"]),
                         ("İstanbul", "Örnekilçe", "Örnek", "100", "7"))
        self.assertEqual(oz["tapu_alani_m2"], 1500.0)
        self.assertNotIn("mevkii", oz)                      # boş alan taşınmaz
        self.assertEqual(p["yazim"], {"ondalik": 5, "birim": "derece"})

    def test_bes_ondalik_belirsizlik_uretir_ve_farki_aciklar(self):
        (p,) = parseller.oku(parsel_sorgu_bicimi())
        o = olc(p)
        self.assertAlmostEqual(o["koordinat_adimi_m"], 1.11, delta=0.03)
        self.assertGreater(o["alan_belirsizligi_m2"], 15.0)
        self.assertLess(o["alan_belirsizligi_m2"], 80.0)
        self.assertLessEqual(abs(o["fark_m2"]), o["alan_belirsizligi_m2"])
        self.assertIn("İÇİNDE", o["fark_yorumu"])

    def test_belirsizlik_formulu_monte_carlo_ile_tutar(self):
        """Türetilen 2σ, yuvarlamanın gerçek etkisinin dağılımını kapsamalı."""
        import random
        random.seed(3)
        (p,) = parseller.oku(parsel_sorgu_bicimi())
        pay = olc(p)["alan_belirsizligi_m2"]
        disari = 0
        for _ in range(300):
            ox, oy = random.uniform(0, 1.2), random.uniform(0, 1.2)       # ızgaraya göre rastgele konum
            halka = []
            for dx, dy in ((0, 0), (60, 0), (60, 25), (0, 25)):
                enlem, boylam = geo.tm_geri(415000.0 + ox + dx, 4545000.0 + oy + dy, 30)
                halka.append((round(boylam, 5), round(enlem, 5)))
            alan = olc({"oznitelik": {}, "cokgenler": [[halka]], "yazim": {"ondalik": 5, "birim": "derece"}})["alan_m2"]
            disari += abs(alan - 1500.0) > pay
        self.assertLess(disari / 300.0, 0.12)               # 2σ ≈ %95; düzgün dağılımda biraz daha dar

    def test_tam_hassasiyette_not_yok(self):
        (p,) = parseller.oku(parsel_sorgu_bicimi(ondalik=9))
        self.assertNotIn("alan_belirsizligi_m2", olc(p))

    def test_kaba_dosyada_kroki_santimetre_yazmaz(self):
        (p,) = parseller.oku(parsel_sorgu_bicimi())
        svg, _ = ciz(p)
        self.assertIn("≈", svg)
        self.assertIn("Hassasiyet: koordinat ızgarası", svg)
        self.assertIn("1.500,00 m²", svg)                   # etikette dosyadaki kayıtlı alan


@unittest.skipIf(harc.durum()["hata"], "tarife verisi yüklenemedi")
class Harc(unittest.TestCase):
    def test_satis_iki_yukumlu_ve_matrah_tabani(self):
        oran = harc._TH["satis"]["oran_binde"]
        h = harc.tapu_harci("satis", 3_000_000.0, emlak_vergi_degeri=4_000_000.0)
        self.assertEqual(h["matrah_tl"], 4_000_000.0)
        self.assertEqual(len(h["kalemler"]), 2)
        self.assertAlmostEqual(h["kalemler"][0]["tutar_tl"], 4_000_000.0 * oran / 1000.0, places=2)
        self.assertAlmostEqual(h["toplam_tl"], 2 * 4_000_000.0 * oran / 1000.0, places=2)
        self.assertTrue(h["dayanak"] and h["alinti"])
        self.assertTrue(any("emlak vergisi" in n for n in h["notlar"]))

    def test_ipotek_tek_yukumlu(self):
        h = harc.tapu_harci("ipotek", 1_000_000.0)
        self.assertEqual(len(h["kalemler"]), 1)
        self.assertAlmostEqual(h["toplam_tl"], 1_000_000.0 * harc._TH["ipotek"]["oran_binde"] / 1000.0, places=2)

    def test_katsayi_olmadan_tapu_islemi_hesaplanmaz(self):
        d = harc.doner_sermaye("1.1.2.1")
        self.assertEqual(d["eksik"], "yoresel_katsayi")
        self.assertNotIn("toplam_tl", d)

    def test_formul_kaliplari(self):
        g, i = harc._DS["GOSTERGE"]["tutar_tl"], harc._DS["ILAVE-GOSTERGE"]["tutar_tl"]
        self.assertAlmostEqual(harc.doner_sermaye("1.1.2.1", 1.5)["toplam_tl"], round(g * 1.5, 2), places=2)
        self.assertAlmostEqual(harc.doner_sermaye("1.5", 1.5, adet=10)["toplam_tl"],
                               round(g * 1.5, 2) + 9 * round(i * 1.5, 2), places=2)
        self.assertAlmostEqual(harc.doner_sermaye("1.3.1", 2.0)["toplam_tl"], 2 * round(g * 2.0, 2), places=2)

    def test_yk_ve_maktu_isaretleri(self):
        yk = next(r for r in harc._VERI["doner_sermaye"] if " | YK | " in r["alinti"] and r["tutar_tl"])
        m = next(r for r in harc._VERI["doner_sermaye"] if " | M | " in r["alinti"] and r["tutar_tl"])
        self.assertAlmostEqual(harc.doner_sermaye(yk["kod"], 2.0)["toplam_tl"], round(yk["tutar_tl"] * 2.0, 2))
        self.assertAlmostEqual(harc.doner_sermaye(m["kod"], 2.0, adet=3)["toplam_tl"], round(m["tutar_tl"] * 3, 2))

    def test_her_satir_alinti_ve_kaynak_tasir(self):
        for r in harc._VERI["doner_sermaye"] + harc._VERI["tapu_harci"]:
            self.assertTrue(r.get("alinti"), r["kod"])
            self.assertTrue(r.get("kaynak"), r["kod"])

    def test_arac_ve_arama(self):
        out = json.loads(server._t_tarife_ara({"sorgu": "ipotek"}))
        self.assertTrue(any(s["kod"] == "ipotek" for s in out["sonuclar"]))
        hesap = json.loads(server._t_harc({"tapu_harci_islem": "satis", "bedel": 1_000_000,
                                           "doner_sermaye_kod": "1.1.2.1"}))
        self.assertNotIn("genel_toplam_tl", hesap)           # katsayı yok → toplam uydurulmaz
        self.assertIn("eksik", hesap["doner_sermaye"])
        with self.assertRaises(McpError):
            server._t_harc({})


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(harc.durum()["hata"], "tarife verisi yüklenemedi")
class MatrahKurallari(unittest.TestCase):
    """v0.3.0 incelemesi: m. 63/2 tabanı her satıra uygulanıyordu; ipotek 10-16 kat şişiyordu."""

    def test_ipotek_ve_kira_emlak_vergisi_tabanina_takilmaz(self):
        for kod in ("ipotek", "ipotek_derece_degisikligi", "kira_serhi"):
            h = harc.tapu_harci(kod, 100_000.0, emlak_vergi_degeri=5_000_000.0)
            self.assertEqual(h["matrah_tl"], 100_000.0, kod)

    def test_kayitli_deger_satirlari_emlak_vergisi_ister(self):
        with self.assertRaises(harc.HarcHatasi):
            harc.tapu_harci("bagis", 1_000_000.0)
        self.assertEqual(harc.tapu_harci("bagis", 0, emlak_vergi_degeri=750_000.0)["matrah_tl"], 750_000.0)

    def test_ust_hakki_alt_ve_ust_sinir(self):
        self.assertEqual(harc.tapu_harci("ust_hakki_daimi_mustakil", 10_000.0, 1_000_000.0)["matrah_tl"], 500_000.0)
        self.assertEqual(harc.tapu_harci("ust_hakki_daimi_mustakil", 9_000_000.0, 1_000_000.0)["matrah_tl"], 2_000_000.0)

    def test_ayni_sermaye_iki_taraf(self):
        self.assertEqual(len(harc.tapu_harci("ayni_sermaye", 1_000_000.0)["kalemler"]), 2)

    def test_on_tl_kesri(self):
        self.assertEqual(harc.tapu_harci("ipotek", 100_009.99)["matrah_tl"], 100_000.0)

    def test_maktu_adetle_carpilir(self):
        tek = harc.tapu_harci("cins_degisikligi_bina", 0)["toplam_tl"]
        self.assertAlmostEqual(harc.tapu_harci("cins_degisikligi_bina", 0, adet=12)["toplam_tl"], 12 * tek, places=2)

    def test_kural_satirlari_hesaplanmaz(self):
        for kod in ("satis_oran_dayanagi", "asgari_nispi_harc", "matrah_alt_siniri"):
            with self.assertRaises(harc.HarcHatasi, msg=kod):
                harc.tapu_harci(kod, 1_000_000.0)

    def test_arac_girdi_dogrulamasi(self):
        import json as _json
        from mcpcore import McpError
        for kotu in ({"bedel": -5}, {"bedel": float("nan")}, {"bedel": 1e308}, {"adet": 0}, {"adet": 2.5},
                     {"bedel": True}):
            with self.assertRaises(McpError, msg=kotu):
                server._t_harc(dict({"tapu_harci_islem": "ipotek", "bedel": 1000}, **kotu))
        h = _json.loads(server._t_harc({"tapu_harci_islem": "ipotek", "bedel": "3.000.000"}))
        self.assertEqual(h["tapu_harci"]["matrah_tl"], 3_000_000.0)


@unittest.skipIf(harc.durum()["hata"], "tarife verisi yüklenemedi")
class IslemMenusu(unittest.TestCase):
    """Arayüz yalnız th_girdisi/ds_girdisi'nin istediği kutuları açar: o kutularla her hesap
    çalışmalı, istenmeyen kutu hiçbir hesabın ön koşulu olmamalı."""

    def _hesap(self, th, ds, g, yk="1,85"):
        args = {"tapu_harci_islem": th, "doner_sermaye_kod": ds}
        if g["tutar"]:
            args["bedel"] = "2.000.000"                     # arayüz metni olduğu gibi gönderir
        if g["evd"] == "zorunlu":
            args["emlak_vergi_degeri"] = "1.250.000"
        if g["yoresel_katsayi"]:
            args["yoresel_katsayi"] = yk
        return json.loads(server._t_harc({k: v for k, v in args.items() if v}))

    def test_menudeki_her_islem_yalniz_istedigi_kutularla_hesaplanir(self):
        menu = harc.islemler()["islemler"]
        self.assertEqual(len(menu), len(harc.ISLEMLER))     # veri değişip satır düşerse fark edilir
        for i in menu:
            for ds in filter(None, {i["doner_sermaye"], i["doner_sermaye_kat"]}):
                h = self._hesap(i["tapu_harci"], ds, i)
                self.assertIsNotNone(h["tapu_harci"]["toplam_tl"], i["kod"])
                self.assertIsNotNone(h.get("genel_toplam_tl"), i["kod"])
            if not i["doner_sermaye"]:
                self.assertFalse(i["yoresel_katsayi"], i["kod"])
                self.assertIsNotNone(self._hesap(i["tapu_harci"], None, i)["tapu_harci"]["toplam_tl"])

    def test_kutu_secimi_matrah_kuralini_izler(self):
        kod = {i["kod"]: i for i in harc.islemler()["islemler"]}
        self.assertEqual((kod["satis"]["tutar"], kod["satis"]["evd"]), ("Satış bedeli", "sinir"))
        self.assertEqual((kod["bagis"]["tutar"], kod["bagis"]["evd"]), (None, "zorunlu"))
        self.assertEqual((kod["ipotek"]["tutar"], kod["ipotek"]["evd"]), ("Borç tutarı", None))
        self.assertEqual((kod["terkin"]["tutar"], kod["terkin"]["evd"], kod["terkin"]["yoresel_katsayi"]),
                         (None, None, False))

    def test_tarifenin_her_satiri_kendi_kutulariyla_hesaplanir(self):
        """"Diğer işlem" yolu: aramada gelen her hesaplanabilir satır."""
        for r in harc._VERI["tapu_harci"]:
            g = harc.th_girdisi(r["kod"])
            if g is None:
                with self.assertRaises(harc.HarcHatasi, msg=r["kod"]):
                    harc.tapu_harci(r["kod"], 1_000_000.0, 500_000.0)
                continue
            h = harc.tapu_harci(r["kod"], 1_000_000.0 if g["tutar"] else 0.0,
                                500_000.0 if g["evd"] == "zorunlu" else None, adet=2 if g["adet"] else 1)
            self.assertGreater(h["toplam_tl"], 0, r["kod"])
        for r in harc._VERI["doner_sermaye"]:
            g = harc.ds_girdisi(r["kod"])
            d = harc.doner_sermaye(r["kod"], 1.5 if g and g["yoresel_katsayi"] else None, adet=2)
            self.assertEqual(g is not None, d.get("toplam_tl") is not None, r["kod"])

    def test_arama_kutu_bilgisini_tasir(self):
        out = json.loads(server._t_tarife_ara({"sorgu": "satış"}))
        satis = next(s for s in out["sonuclar"] if s["kod"] == "satis")
        self.assertEqual(satis["girdi"]["evd"], "sinir")
        self.assertTrue(any(s["tablo"] == "doner_sermaye" and s["girdi"] for s in out["sonuclar"]))


class HukukGuncel(unittest.TestCase):
    """7571 ve 7579 değişiklikleri: yanlış hak düşürücü süre kaçırılmış bir dava demektir."""

    def test_onalim_mutlak_sure_bir_yil(self):
        import tkgm_hukuk as hukuk
        k = hukuk.KONULAR["onalim"]
        self.assertIn("BİR YIL", k["sure"])
        self.assertNotIn("üç ay / iki yıl", k["sure"])
        self.assertIn("geçici m. 1", k["sure"])   # 25.12.2025 öncesi satışlarda eski hüküm sürer
        self.assertIn("734/2", k["uyari"])

    def test_izin_yolu_elektronik_kabul_beyani(self):
        import tkgm_kaynak as kaynak
        self.assertIn("elektronik kabul beyanı", kaynak.canli_durum()["yol"])
