"""tkgm_harita testleri — varsayılan olarak ağsız: python -m unittest discover -s tests

TIFF okuyucu, testin kendi yazdığı küçük bir karolu float32 Deflate TIFF'e karşı
sınanır. Yazıcı ile okuyucu aynı belgeden (TIFF 6.0 + Adobe TechNote 3) bağımsız
yazıldı; kodlayıcı burada, çözücü modülde durur. Gerçek Copernicus karosuna karşı
tek sınama canlıdır ve TKGM_CANLI_TEST=1 ister.
"""

import ast
import json
import math
import os
import re
import struct
import sys
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# test_offline, `python -m unittest tests.test_harita` biçiminde çağrıldığında da bulunsun.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkgm_geo as geo  # noqa: E402
import tkgm_harita as h  # noqa: E402
import tkgm_parsel as parseller  # noqa: E402
from test_offline import ornek_geojson  # noqa: E402

_SALDIRI = "</script><script>alert(1)</script>"


# --------------------------------------------------------------------------- #
# Sentetik TIFF yazıcı                                                         #
# --------------------------------------------------------------------------- #
def _satir_kodla(degerler, ongorucu, bo):
    n = len(degerler)
    if ongorucu == 3:
        buyuk_uclu = struct.pack(">%df" % n, *degerler)
        serit = bytearray(4 * n)
        for k in range(4):
            serit[k * n:(k + 1) * n] = buyuk_uclu[k::4]
        fark = bytearray(serit)
        for i in range(len(serit) - 1, 0, -1):
            fark[i] = (serit[i] - serit[i - 1]) & 255
        return bytes(fark)
    if ongorucu == 2:
        w = struct.unpack("%s%dI" % (bo, n), struct.pack("%s%df" % (bo, n), *degerler))
        return struct.pack("%s%dI" % (bo, n), *([w[0]] + [(w[i] - w[i - 1]) & 0xFFFFFFFF
                                                          for i in range(1, n)]))
    return struct.pack("%s%df" % (bo, n), *degerler)


def _karolar(goruntu, karo, ongorucu, bo):
    boy, en = len(goruntu), len(goruntu[0])
    out = []
    for ky in range(-(-boy // karo)):
        for kx in range(-(-en // karo)):
            ham = b""
            for r in range(karo):
                satir = [0.0] * karo                       # kenar karoları tam boya doldurulur
                if ky * karo + r < boy:
                    parca = goruntu[ky * karo + r][kx * karo:(kx + 1) * karo]
                    satir[:len(parca)] = parca
                ham += _satir_kodla(satir, ongorucu, bo)
            out.append(zlib.compress(ham, 6))
    return out


def tiff_uret(seviyeler, karo=16, ongorucu=3, bo="<", bag=(32.0, 40.0), olcek=1.0 / 120.0,
              sikistirma=8, sihir=42, nokta_mi=True):
    """seviyeler[0] tam çözünürlük, sonrakiler küçültülmüş görünüm. IFD'ler veriden önce (COG düzeni)."""
    karolar = [_karolar(g, karo, ongorucu, bo) for g in seviyeler]

    def kur(ofsetler):
        govde = bytearray((b"II" if bo == "<" else b"MM") + struct.pack(bo + "HI", sihir, 8))
        for no, g in enumerate(seviyeler):
            e = [(256, 3, [len(g[0])]), (257, 3, [len(g)]), (258, 3, [32]), (259, 3, [sikistirma]),
                 (277, 3, [1]), (317, 3, [ongorucu]), (322, 3, [karo]), (323, 3, [karo]),
                 (324, 4, ofsetler[no]), (325, 4, [len(k) for k in karolar[no]]), (339, 3, [3])]
            if no:
                e.append((254, 4, [1]))
            else:
                e += [(33550, 12, [olcek, olcek, 0.0]), (33922, 12, [0.0, 0.0, 0.0, bag[0], bag[1], 0.0]),
                      (34735, 3, [1, 1, 0, 2, 1024, 0, 1, 2, 1025, 0, 1, 2 if nokta_mi else 1])]
            e.sort()
            ek_konum = len(govde) + 2 + 12 * len(e) + 4
            ifd, ek = struct.pack(bo + "H", len(e)), b""
            for etiket_no, tip, degerler in e:
                ham = struct.pack("%s%d%s" % (bo, len(degerler), {3: "H", 4: "I", 12: "d"}[tip]), *degerler)
                ifd += struct.pack(bo + "HHI", etiket_no, tip, len(degerler))
                if len(ham) <= 4:
                    ifd += ham.ljust(4, b"\0")
                else:
                    ifd += struct.pack(bo + "I", ek_konum + len(ek))
                    ek += ham
            sonraki = ek_konum + len(ek) if no + 1 < len(seviyeler) else 0
            govde += ifd + struct.pack(bo + "I", sonraki) + ek
        return govde

    bas = len(kur([[0] * len(k) for k in karolar]))
    ofsetler, konum = [], bas
    for k in karolar:
        ofsetler.append([])
        for parca in k:
            ofsetler[-1].append(konum)
            konum += len(parca)
    return bytes(kur(ofsetler)) + b"".join(p for k in karolar for p in k), ofsetler


def getirici(dosyalar, gunluk=None):
    def al(url, bas, uzunluk):
        if url not in dosyalar:
            raise h._KaroYok("yok: %s" % url)
        if gunluk is not None:
            gunluk.append((url, bas, uzunluk))
        return dosyalar[url][bas:bas + uzunluk]
    return al


def _goruntu(en, boy):
    # 0,25'in katları float32'de tamdır: "tam eşit" sınaması yuvarlamaya takılmaz.
    return [[1000.0 + 0.25 * j - 0.5 * i + (7.75 if (i * j) % 5 == 0 else 0.0) for j in range(en)]
            for i in range(boy)]


def _duzlem(boylam, enlem):
    # Eğim dikişi görünür kılacak kadar büyük, 43° boylamda bile 9000 m akıl sınırının altında.
    return 200.0 + 400.0 * (boylam - 32.0) + 300.0 * (enlem - 39.0)


def _sentetik_karo(enlem_tam, boylam_tam, n=120):
    """1°'lik karo, n×n piksel + yarı çözünürlükte görünüm (GDAL gibi 2×2 ortalama)."""
    tam = [[_duzlem(boylam_tam + j / float(n), enlem_tam + 1 - i / float(n)) for j in range(n)]
           for i in range(n)]
    yari = [[(tam[2 * i][2 * j] + tam[2 * i][2 * j + 1] + tam[2 * i + 1][2 * j] + tam[2 * i + 1][2 * j + 1]) / 4.0
             for j in range(n // 2)] for i in range(n // 2)]
    return tiff_uret([tam, yari], karo=32, bag=(float(boylam_tam), enlem_tam + 1.0), olcek=1.0 / n)[0]


# --------------------------------------------------------------------------- #
class TiffOkuyucu(unittest.TestCase):
    def _gidis_donus(self, ongorucu, bo):
        g = _goruntu(40, 24)                                # 16'lık karoda 3×2 karo, kenarlar dolgulu
        veri, _ = tiff_uret([g], ongorucu=ongorucu, bo=bo)
        al = getirici({"u": veri})
        cog = h.cog_ac("u", al)
        self.assertEqual((cog["seviyeler"][0]["en"], cog["seviyeler"][0]["boy"]), (40, 24))
        self.assertEqual(h.cog_pencere(cog, 0, 0, 0, 40, 24, al), g)
        self.assertEqual(h.cog_pencere(cog, 0, 13, 9, 35, 20, al), [s[13:35] for s in g[9:20]])

    def test_ongorucu_3_kayan_nokta(self):
        self._gidis_donus(3, "<")

    def test_ongorucu_3_buyuk_uclu_dosya(self):
        self._gidis_donus(3, ">")

    def test_ongorucu_2_yatay_fark(self):
        self._gidis_donus(2, "<")

    def test_ongorucu_1_yok(self):
        self._gidis_donus(1, "<")
        self._gidis_donus(1, ">")

    def test_konum_piksel_merkezinden_alan_kenarina_cevrilir(self):
        veri, _ = tiff_uret([_goruntu(32, 32)])
        cog = h.cog_ac("u", getirici({"u": veri}))
        self.assertAlmostEqual(cog["bati_kenar"], 32.0 - 0.5 / 120.0, places=12)
        self.assertAlmostEqual(cog["kuzey_kenar"], 40.0 + 0.5 / 120.0, places=12)
        alan, _ = tiff_uret([_goruntu(32, 32)], nokta_mi=False)
        self.assertAlmostEqual(h.cog_ac("u", getirici({"u": alan}))["bati_kenar"], 32.0, places=12)

    def test_yalniz_degen_karo_ve_gereken_onek_iner(self):
        g = _goruntu(64, 64)
        veri, ofsetler = tiff_uret([g])
        gunluk = []
        al = getirici({"u": veri}, gunluk)
        cog = h.cog_ac("u", al)
        del gunluk[:]
        self.assertEqual(h.cog_pencere(cog, 0, 17, 2, 20, 4, al), [s[17:20] for s in g[2:4]])
        for _, bas, uzunluk in gunluk:                     # yalnız 1 numaralı karonun aralığı
            self.assertGreaterEqual(bas, ofsetler[0][1])
            self.assertLessEqual(bas + uzunluk, ofsetler[0][2])

    def test_bigtiff_ve_desteklenmeyen_sikistirma_acik_hata(self):
        buyuk, _ = tiff_uret([_goruntu(16, 16)], sihir=43)
        with self.assertRaisesRegex(h.HaritaHatasi, "BigTIFF"):
            h.cog_ac("u", getirici({"u": buyuk}))
        lzw, _ = tiff_uret([_goruntu(16, 16)], sikistirma=5)
        with self.assertRaisesRegex(h.HaritaHatasi, "sıkıştırma"):
            h.cog_ac("u", getirici({"u": lzw}))
        with self.assertRaises(h.HaritaHatasi):
            h.cog_ac("u", getirici({"u": b"TIFF degil, duz metin"}))

    def test_bozuk_deflate_harita_hatasi_olur(self):
        veri, ofsetler = tiff_uret([_goruntu(16, 16)])
        bozuk = veri[:ofsetler[0][0]] + b"\xff" * (len(veri) - ofsetler[0][0])
        al = getirici({"u": bozuk})
        with self.assertRaises(h.HaritaHatasi):
            h.cog_pencere(h.cog_ac("u", al), 0, 0, 0, 4, 4, al)


class YenidenOrnekleme(unittest.TestCase):
    def test_cift_dogrusal(self):
        z = [[0.0, 10.0], [20.0, 50.0]]
        self.assertEqual(h.cift_dogrusal(z, 0, 0), 0.0)
        self.assertEqual(h.cift_dogrusal(z, 1, 1), 50.0)
        self.assertAlmostEqual(h.cift_dogrusal(z, 0.5, 0.5), 20.0)
        self.assertAlmostEqual(h.cift_dogrusal(z, 0.0, 0.25), 2.5)
        self.assertAlmostEqual(h.cift_dogrusal(z, 0.5, 1.0), 30.0)
        self.assertEqual(h.cift_dogrusal(z, -3, 9), 10.0)   # dışarıda kenetlenir

    def test_ucgen_yuzey_koselerde_ve_kosegende_tutarli(self):
        z = [[0.0, 10.0], [20.0, 50.0]]
        self.assertEqual(h._yuzey_kotu(z, 0, 0), 0.0)
        self.assertEqual(h._yuzey_kotu(z, 1, 1), 50.0)
        self.assertAlmostEqual(h._yuzey_kotu(z, 0.5, 0.5), 15.0)   # köşegen: (10+20)/2, çift doğrusal 20 verirdi
        self.assertAlmostEqual(h._yuzey_kotu(z, 0.25, 0.25), 7.5)

    def _dosyalar(self, karolar, kume=0):
        return {h._karo_url(h.VERI_KUMELERI[kume], e, b): _sentetik_karo(e, b) for e, b in karolar}

    def _duzleme_esit(self, izgara, delta=0.011):
        for i, enlem in enumerate(izgara["enlemler"]):
            for j, boylam in enumerate(izgara["boylamlar"]):
                self.assertAlmostEqual(izgara["z"][i][j], _duzlem(boylam, enlem), delta=delta)

    def test_izgara_duzlemi_aynen_verir(self):
        izgara = h.dem_izgara(32.30, 39.40, 32.42, 39.48, 24, al=getirici(self._dosyalar([(39, 32)])))
        self.assertEqual(len(izgara["boylamlar"]), 24)
        self.assertGreater(izgara["enlemler"][0], izgara["enlemler"][-1])      # kuzeyden güneye
        self.assertEqual(izgara["okunan_cozunurluk_m"], 30.0)
        self.assertIn("Copernicus WorldDEM-30", izgara["atif"])
        self.assertIn("DLR", izgara["atif"])
        self._duzleme_esit(izgara)

    def test_buyuk_kutu_kucultulmus_gorunumu_secer(self):
        gunluk = []
        dosyalar = self._dosyalar([(39, 32)])
        izgara = h.dem_izgara(32.20, 39.30, 32.70, 39.70, 16, al=getirici(dosyalar, gunluk))
        self.assertEqual(izgara["okunan_cozunurluk_m"], 60.0)
        self._duzleme_esit(izgara)

    def test_iki_karoya_yayilan_kutu_dikissiz_birlesir(self):
        al = getirici(self._dosyalar([(39, 32), (39, 33)]))
        izgara = h.dem_izgara(32.95, 39.45, 33.05, 39.55, 40, al=al)
        self.assertTrue(any(32.9917 < b < 33.0 for b in izgara["boylamlar"]))   # son merkez ile dikiş arası
        self._duzleme_esit(izgara)
        dort = getirici(self._dosyalar([(39, 32), (39, 33), (38, 32), (38, 33)]))
        self._duzleme_esit(h.dem_izgara(32.97, 38.97, 33.03, 39.03, 30, al=dort))

    def test_son_merkez_ile_dikis_arasi_komsu_karoyu_ister(self):
        gunluk = []
        # Gerçek veride son merkez 33 − 1/3600 = 32,99972'dedir; 32,9999 onun doğusunda kalır.
        dosyalar = self._dosyalar([(39, 32), (39, 33)])
        h.dem_izgara(32.990, 39.5, 32.9999, 39.51, 8, al=getirici(dosyalar, gunluk))
        self.assertTrue(any("E033" in u for u, _, _ in gunluk))
        del gunluk[:]
        h.dem_izgara(32.990, 39.5, 32.9990, 39.51, 8, al=getirici(dosyalar, gunluk))
        self.assertFalse(any("E033" in u for u, _, _ in gunluk))   # gerekmeyen karo açılmaz

    def test_yayimlanmamis_karo_glo90a_duser(self):
        dosyalar = self._dosyalar([(40, 43)], kume=1)       # GLO-30'da yok, GLO-90'da var (Kars)
        izgara = h.dem_izgara(43.05, 40.55, 43.15, 40.65, 16, al=getirici(dosyalar))
        self.assertEqual(izgara["kaynak"], "Copernicus DEM GLO-90")
        self.assertEqual(izgara["kaynak_cozunurluk_m"], 90)
        self.assertIn("WorldDEM-90", izgara["atif"])
        self.assertTrue(any("GLO-90" in u for u in izgara["uyari"]))

    def test_deniz_karosu_sifir_ve_uyari_hic_karo_yoksa_hata(self):
        izgara = h.dem_izgara(32.95, 39.45, 33.05, 39.55, 20, al=getirici(self._dosyalar([(39, 32)])))
        self.assertEqual(izgara["z"][0][-1], 0.0)
        self.assertAlmostEqual(izgara["z"][0][0], _duzlem(izgara["boylamlar"][0], izgara["enlemler"][0]), delta=0.011)
        self.assertTrue(any("E033" in u for u in izgara["uyari"]))
        # Deniz karosu varken de, AÇILMAMIŞ komşunun kenarı 0 m'ye değil en yakın hücreye kenetlenir:
        # kutu güneyde 39°'a bir pikselden yakın değil, N38 açılmaz; en alt satır yine düzlemdedir.
        self.assertGreater(min(izgara["z"][-1][:5]), 300.0)
        with self.assertRaisesRegex(h.HaritaHatasi, "karosu yok"):
            h.dem_izgara(30.2, 34.2, 30.3, 34.3, al=getirici({}))

    def test_gecersiz_ve_asiri_kutu(self):
        for kutu in ((33.0, 39.0, 32.0, 40.0), (39.9, 32.8, 39.95, 32.85 + 90), ("a", 1, 2, 3)):
            with self.assertRaises(h.HaritaHatasi):
                h.dem_izgara(*kutu, al=getirici({}))
        with self.assertRaisesRegex(h.HaritaHatasi, "çok büyük"):
            h.dem_izgara(30.5, 38.5, 33.5, 40.5, al=getirici({}))

    def test_kucuk_kutu_en_az_sekiz_kaynak_hucresine_genisler(self):
        izgara = h.dem_izgara(32.5, 39.5, 32.5001, 39.5001, 8, al=getirici(self._dosyalar([(39, 32)])))
        b, g, d, k = izgara["kutu"]
        self.assertAlmostEqual(d - b, 8.0 / 3600.0, places=6)
        self.assertAlmostEqual(k - g, 8.0 / 3600.0, places=6)


class Ag(unittest.TestCase):
    def setUp(self):
        self.asil = h._indir
        h.onbellegi_bosalt()

    def tearDown(self):
        h._indir = self.asil
        h.onbellegi_bosalt()

    def test_izinsiz_adres_aga_cikmadan_reddedilir(self):
        for url in ("https://example.org/x.tif", "https://copernicus-dem-30m.s3.amazonaws.com.evil.org/x"):
            with self.assertRaisesRegex(h.HaritaHatasi, "İzin verilmeyen"):
                h._indir(url, 0, 9)

    def test_blok_onbellegi_ortak_oneki_yeniden_indirmez(self):
        cagrilar = []

        def sahte(url, bas, son):
            cagrilar.append((bas, son))
            return bytes(bytearray((i // h._BLOK) & 255 for i in range(bas, son + 1)))

        h._indir = sahte
        self.assertEqual(h._http_aralik("u", 10, 5), b"\0" * 5)
        self.assertEqual(h._http_aralik("u", 100, 50), b"\0" * 50)
        self.assertEqual(cagrilar, [(0, h._BLOK - 1)])                  # ikinci istek önbellekten
        h._http_aralik("u", 3 * h._BLOK, 10)
        del cagrilar[:]
        govde = h._http_aralik("u", h._BLOK - 2, 3 * h._BLOK)           # 0 ve 3 önbellekte; 1-2 tek istekle
        self.assertEqual(cagrilar, [(h._BLOK, 3 * h._BLOK - 1)])
        self.assertEqual(govde[:3], b"\0\0\1")
        self.assertEqual(len(govde), 3 * h._BLOK)


class AraziOzeti(unittest.TestCase):
    def setUp(self):
        (self.p,) = parseller.oku(ornek_geojson())

    def _izgara(self, n, egim_d, egim_k, pay=60.0):
        """Bağımsız metre: TM33 koordinatı. z = 1000 + egim_d·doğu + egim_k·kuzey."""
        b0, e0, b1, e1 = h.parsel_kutusu(self.p, None, pay)
        enlemler = [e1 - (e1 - e0) * i / (n - 1) for i in range(n)]
        boylamlar = [b0 + (b1 - b0) * j / (n - 1) for j in range(n)]
        y0, x0 = geo.tm_ileri(e0, b0, 33)
        z = []
        for enlem in enlemler:
            satir = []
            for boylam in boylamlar:
                y, x = geo.tm_ileri(enlem, boylam, 33)
                satir.append(1000.0 + egim_d * (y - y0) + egim_k * (x - x0))
            z.append(satir)
        return {"enlemler": enlemler, "boylamlar": boylamlar, "z": z, "kaynak": "sentetik"}

    def test_egik_duzlem_egim_ve_baki(self):
        ozet = h.arazi_ozeti(self.p, self._izgara(41, 0.10, 0.05))
        self.assertEqual(ozet["yontem"], "izgara")
        self.assertAlmostEqual(ozet["ortalama_egim_yuzde"], 100.0 * math.hypot(0.10, 0.05), delta=0.1)
        self.assertEqual(ozet["hakim_baki"], "GB")           # doğuya ve kuzeye yükselen yamaç güneybatıya bakar
        self.assertAlmostEqual(ozet["baki_derece"], 243.4, delta=1.0)
        self.assertAlmostEqual(ozet["kot_farki_m"], ozet["max_kot_m"] - ozet["min_kot_m"], places=1)
        self.assertTrue(ozet["min_kot_m"] < ozet["ortalama_kot_m"] < ozet["max_kot_m"])
        self.assertIn("DSM", ozet["not"])
        self.assertIn("yerine geçmez", ozet["not"])

    def test_sekiz_yon_ve_duz(self):
        for egim_d, egim_k, beklenen in ((0.0, 0.08, "G"), (0.0, -0.08, "K"), (0.08, 0.0, "B"),
                                         (-0.08, 0.0, "D"), (-0.06, -0.06, "KD"), (0.06, -0.06, "KB"),
                                         (-0.06, 0.06, "GD"), (0.002, 0.001, "düz")):
            self.assertEqual(h.arazi_ozeti(self.p, self._izgara(21, egim_d, egim_k))["hakim_baki"],
                             beklenen, (egim_d, egim_k))

    def test_hucreden_kucuk_parsel_koselerden_orneklenir(self):
        ozet = h.arazi_ozeti(self.p, self._izgara(3, 0.10, 0.05, pay=150.0))   # ~170 m hücre, 40×30 parsel
        self.assertEqual(ozet["yontem"], "kose+etiket")
        self.assertIn(ozet["ornek_sayisi"], (5, 6))          # 4 köşe + etiket noktası (+ içe düşen tek düğüm)
        self.assertAlmostEqual(ozet["ortalama_egim_yuzde"], 11.18, delta=0.15)
        # 40×30 m parsel, 20° dönük: köşeler arası en büyük kot farkı düzlemden hesaplanır.
        self.assertAlmostEqual(ozet["kot_farki_m"], 4.0, delta=1.2)

    def test_izgara_disindaki_parsel_acik_hata(self):
        izgara = self._izgara(5, 0.1, 0.0)
        izgara["boylamlar"] = [b + 1.0 for b in izgara["boylamlar"]]
        with self.assertRaises(h.HaritaHatasi):
            h.arazi_ozeti(self.p, izgara)

    def test_metre_derece_bagimsiz_egrilik_yaricaplarina_esit(self):
        e2 = geo._F * (2.0 - geo._F)
        fi = math.radians(39.93)
        m = geo._A * (1 - e2) / (1 - e2 * math.sin(fi) ** 2) ** 1.5
        n = geo._A / math.sqrt(1 - e2 * math.sin(fi) ** 2)
        mb, me = h.metre_derece(39.93, 32.85)
        self.assertAlmostEqual(mb, math.radians(1.0) * n * math.cos(fi), delta=0.01)
        self.assertAlmostEqual(me, math.radians(1.0) * m, delta=0.01)


class Html(unittest.TestCase):
    def setUp(self):
        (self.p,) = parseller.oku(ornek_geojson())
        gj = json.loads(ornek_geojson(donme=20.0))
        for nokta in gj["features"][0]["geometry"]["coordinates"][0]:
            nokta[0] += 0.0006
        gj["features"][0]["properties"].update({"parselNo": "5"})
        (self.komsu,) = parseller.oku(json.dumps(gj))
        self.izgara = AraziOzeti._izgara(self, 33, 0.10, 0.05, pay=80.0)
        self.ozet = h.arazi_ozeti(self.p, self.izgara)

    @staticmethod
    def _veri(sayfa, bas):
        govde = sayfa[sayfa.index(bas) + len(bas):]
        return json.JSONDecoder().raw_decode(govde)[0]

    def test_harita_icerigi(self):
        sayfa = h.harita_html(self.p, [self.komsu])
        for beklenen in ("123/4", "© OpenStreetMap contributors", "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                         "leaflet/1.9.4/leaflet.min.js", 'integrity="sha512-', "maxZoom:19", "fitBounds",
                         "L.control.scale", "1.200,00 m²", "Tarla", "Ankara / Çankaya / Örnek", "md. 4",
                         "röperli"):
            self.assertIn(beklenen, sayfa)
        veri = self._veri(sayfa, "var V=")
        self.assertEqual(veri["komsular"][0]["ad"], "123/5")
        enlem, boylam = veri["parsel"][0][0][0]
        self.assertTrue(39.0 < enlem < 41.0 and 32.0 < boylam < 34.5)   # Leaflet sırası: enlem önce
        self.assertIsNone(re.search(r"\d\.\d{8,}", json.dumps(veri)))    # en çok 7 ondalık
        self.assertLess(len(sayfa), 8000)

    def test_uc_boyut_icerigi(self):
        sayfa = h.arazi_3d_html(self.p, [self.komsu], self.izgara, self.ozet)
        for beklenen in ("123/4", "three@0.160.0", "importmap", "OrbitControls", 'min="1" max="5"', 'value="2"',
                         "sentetik", "Copernicus", "DLR", "md. 4", "DSM", "Hâkim bakı", "GB"):
            self.assertIn(beklenen, sayfa)
        veri = self._veri(sayfa, "const V=")
        self.assertEqual(len(veri["dz"]), veri["nx"] * veri["ny"])
        self.assertEqual(min(veri["dz"]), 0)
        self.assertTrue(all(isinstance(v, int) for v in veri["dz"]))     # 0,1 m'ye yuvarlı desimetre
        self.assertEqual(len(veri["komsular"]), 1)
        # Örtülü çizgi düzlemin 0,5 m üstünde: y = 0,10·doğu + 0,05·kuzey + sabit (x doğu, z güney).
        cizgi = veri["parsel"][0]
        self.assertEqual(cizgi[:3], cizgi[-3:])                          # halka kapanır
        self.assertGreater(len(cizgi) // 3, 140 / 5)                     # ≤5 m'de bir nokta
        sabit = cizgi[1] - 0.10 * cizgi[0] + 0.05 * cizgi[2]
        for k in range(0, len(cizgi), 3):
            self.assertAlmostEqual(cizgi[k + 1], 0.10 * cizgi[k] - 0.05 * cizgi[k + 2] + sabit, delta=0.15)
        taban = min(min(s) for s in self.izgara["z"])
        ex, ey, ez = veri["etiket"]
        self.assertAlmostEqual(ey + taban, h.kot_al(self.izgara, *reversed(h._etiket_noktasi(self.p))), delta=0.1)
        self.assertLess(len(sayfa), 40000)

    def test_tkgm_adresi_yok(self):
        for sayfa in (h.harita_html(self.p, [self.komsu]),
                      h.arazi_3d_html(self.p, [self.komsu], self.izgara, self.ozet)):
            self.assertNotIn("tkgm.gov.tr", sayfa)
            adresler = set(re.findall(r"https?://[^/\"'\s]+", sayfa))
            self.assertLessEqual(adresler, {"https://cdnjs.cloudflare.com", "https://tile.openstreetmap.org",
                                           "https://cdn.jsdelivr.net"})
        with open(h.__file__.replace(".pyc", ".py"), encoding="utf-8") as fh:
            self.assertNotIn("tkgm.gov.tr", fh.read())

    def test_script_kacisi(self):
        gj = json.loads(ornek_geojson())
        gj["features"][0]["properties"].update({
            "nitelik": _SALDIRI, "adaNo": _SALDIRI, "mahalleAd": "<img src=x onerror=alert(1)>",
            "parselNo": " '\"--><svg onload=alert(1)>"})
        (kotu,) = parseller.oku(json.dumps(gj))
        for sayfa, betik_sayisi in ((h.harita_html(kotu, [kotu, self.komsu]), 2),
                                    (h.arazi_3d_html(kotu, [self.komsu], self.izgara, self.ozet), 2)):
            self.assertEqual(sayfa.count("</script>"), betik_sayisi)
            self.assertEqual(len(re.findall(r"<script\b", sayfa)), betik_sayisi)
            for yasak in ("<script>alert", "<img", "<svg", " "):
                self.assertNotIn(yasak, sayfa)
        veri = self._veri(h.harita_html(kotu), "var V=")
        self.assertTrue(veri["ad"].startswith(_SALDIRI))                 # kaçış veriyi bozmaz, yalnız saklar


class ModulKurallari(unittest.TestCase):
    def test_butun_importlar_modul_basinda(self):
        """Toplayıcı arka ucu yükledikten sonra klasörü sys.path'ten çıkarır; işlev içi
        import yükleme anında değil çağrı anında patlar ve testte görünmez."""
        with open(h.__file__.replace(".pyc", ".py"), encoding="utf-8") as fh:
            agac = ast.parse(fh.read())
        for dugum in ast.walk(agac):
            if isinstance(dugum, (ast.FunctionDef, ast.ClassDef)):
                for ic in ast.walk(dugum):
                    self.assertNotIsInstance(ic, (ast.Import, ast.ImportFrom))


@unittest.skipUnless(os.environ.get("TKGM_CANLI_TEST") == "1", "canlı: TKGM_CANLI_TEST=1")
class Canli(unittest.TestCase):
    def test_ankara_kotlari_makul(self):
        h.onbellegi_bosalt()
        izgara = h.dem_izgara(32.8490, 39.9295, 32.8510, 39.9305, 32)
        duz = [v for s in izgara["z"] for v in s]
        print("\nCANLI Ankara 32.85D 39.93K: min %.1f maks %.1f ort %.1f m | %d×%d ızgara | %d bayt, %d istek | %s"
              % (min(duz), max(duz), sum(duz) / len(duz), len(izgara["z"]), len(izgara["z"][0]),
                 izgara["indirilen_bayt"], izgara["istek_sayisi"], izgara["kaynak"]))
        self.assertTrue(all(700.0 < v < 1300.0 for v in duz))
        self.assertGreater(izgara["indirilen_bayt"], 0)
        tekrar = h.dem_izgara(32.8490, 39.9295, 32.8510, 39.9305, 32)
        self.assertEqual(tekrar["indirilen_bayt"], 0)                    # ikinci çağrı önbellekten
        self.assertEqual(tekrar["z"], izgara["z"])


if __name__ == "__main__":
    unittest.main()
