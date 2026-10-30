"""Tahmini bina kütlesi testleri (ağsız).

PMTiles arşivi ve MVT karoları testte ELLE kodlanır: okuyucu, kendi yazdığımız bir
kodlayıcıya değil biçimin tanımına karşı sınanır. Canlı Overture denemesi
TKGM_CANLI_TEST=1 ile açılır.
"""

import copy
import gzip
import json
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkgm_bina as bina  # noqa: E402
import tkgm_geo as geo  # noqa: E402
import tkgm_harita as h  # noqa: E402
import tkgm_parsel as parseller  # noqa: E402
from test_offline import ornek_geojson  # noqa: E402

SURUM = "2026-08-19.0"
URL = "%s%s/buildings.pmtiles" % (bina.ARSIV_KOKU, SURUM)


# --------------------------------------------------------------------------- #
# Kodlayıcılar — yalnız test                                                   #
# --------------------------------------------------------------------------- #
def vint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def zz(n):
    return (n << 1) ^ (n >> 63)


def alan(no, tel, deger):
    anahtar = vint(no << 3 | tel)
    if tel == 0:
        return anahtar + vint(deger)
    if tel == 1:
        return anahtar + deger
    return anahtar + vint(len(deger)) + deger


def paketli(sayilar):
    return b"".join(vint(s) for s in sayilar)


def deger(v):
    if isinstance(v, bool):
        return alan(7, 0, int(v))
    if isinstance(v, str):
        return alan(1, 2, v.encode("utf-8"))
    if isinstance(v, float):
        return alan(3, 1, struct.pack("<d", v))
    return alan(5, 0, v)


def geometri(halkalar):
    """Halkalar karo pikselinde; MVT'de her halka MoveTo + LineTo + ClosePath."""
    komut, x, y = [], 0, 0
    for halka in halkalar:
        (hx, hy) = halka[0]
        komut += [1 | (1 << 3), zz(hx - x), zz(hy - y)]
        x, y = hx, hy
        komut.append(2 | ((len(halka) - 1) << 3))
        for px, py in halka[1:]:
            komut += [zz(px - x), zz(py - y)]
            x, y = px, py
        komut.append(7 | (1 << 3))
    return komut


def karo_uret(nesneler, katman="building", olcek=4096):
    anahtarlar, degerler, govde = [], [], b""
    for sira, (oz, halkalar) in enumerate(nesneler):
        etiket = []
        for k, v in oz.items():
            if k not in anahtarlar:
                anahtarlar.append(k)
            if v not in degerler:
                degerler.append(v)
            etiket += [anahtarlar.index(k), degerler.index(v)]
        govde += alan(2, 2, alan(1, 0, sira + 1) + alan(2, 2, paketli(etiket)) + alan(3, 0, 3)
                      + alan(4, 2, paketli(geometri(halkalar))))
    kat = (alan(15, 0, 2) + alan(1, 2, katman.encode()) + govde
           + b"".join(alan(3, 2, k.encode()) for k in anahtarlar)
           + b"".join(alan(4, 2, deger(v)) for v in degerler) + alan(5, 0, olcek))
    return alan(3, 2, kat)


def dizin(kayitlar):
    """(karo_no, tekrar, uzunluk, ofset) → PMTiles v3 dizini; ardışık ofset 0 ile yazılır."""
    out = vint(len(kayitlar))
    son = 0
    for no, _, _, _ in kayitlar:
        out += vint(no - son)
        son = no
    out += b"".join(vint(k[1]) for k in kayitlar) + b"".join(vint(k[2]) for k in kayitlar)
    for i, k in enumerate(kayitlar):
        ardisik = i > 0 and k[3] == kayitlar[i - 1][3] + kayitlar[i - 1][2]
        out += vint(0 if ardisik else k[3] + 1)
    return out


def arsiv_uret(karolar, yaprakli=False, max_zoom=14):
    sirali = sorted((bina.karo_no(*zxy), gzip.compress(v)) for zxy, v in karolar.items())
    veri, kayitlar = b"", []
    for no, g in sirali:
        kayitlar.append((no, 1, len(g), len(veri)))
        veri += g
    yaprak = b""
    if yaprakli:
        yaprak = gzip.compress(dizin(kayitlar))
        kok = gzip.compress(dizin([(kayitlar[0][0], 0, len(yaprak), 0)]))
    else:
        kok = gzip.compress(dizin(kayitlar))
    kok_o = 127
    yaprak_o = kok_o + len(kok)
    veri_o = yaprak_o + len(yaprak)
    baslik = struct.pack("<7sB11Q6B4iB2i", b"PMTiles", 3, kok_o, len(kok), yaprak_o, 0, yaprak_o,
                         len(yaprak), veri_o, len(veri), len(kayitlar), len(kayitlar), len(kayitlar),
                         1, 2, 2, 1, 0, max_zoom, 0, 0, 0, 0, 0, 0, 0)
    return baslik + kok + yaprak + veri


def getirici(arsiv, gunluk=None, katalog=None):
    katalog = katalog if katalog is not None else json.dumps({"latest": SURUM}).encode()

    def al(url, bas, uzunluk):
        if gunluk is not None:
            gunluk.append((url, bas, uzunluk))
        if url == bina.KATALOG:
            return katalog
        if url != URL:
            raise bina._ArsivYok("404")
        return arsiv[bas:] if uzunluk is None else arsiv[bas:bas + uzunluk]
    return al


def kare(cx, cy, yari):
    """Karo pikselinde saat yönünde (y aşağı) kare: MVT'de dış halka."""
    return [(cx - yari, cy - yari), (cx + yari, cy - yari), (cx + yari, cy + yari), (cx - yari, cy + yari)]


def kare_ters(cx, cy, yari):
    return list(reversed(kare(cx, cy, yari)))


def karo_sinirlari(z, x, y):
    b0, k0 = bina._piksel_cografi(z, x, y, 0, 0, 4096)
    b1, g1 = bina._piksel_cografi(z, x, y, 4096, 4096, 4096)
    return b0, g1, b1, k0


def _b(b, e, yukseklik=None, kat=None):
    d = 0.00005
    return {"merkez": (b, e), "cokgenler": [[[(b - d, e - d), (b + d, e - d), (b + d, e + d), (b - d, e + d)]]],
            "yukseklik_m": yukseklik, "kat": kat, "kaynak": "OpenStreetMap"}


def ornek_bina_verisi(p):
    """Biri parselin ağırlık merkezinde, üçü ~170-220 m ötede dört kare bina."""
    halka = p["cokgenler"][0][0]
    b, e = geo.agirlik_merkezi(halka[:-1] if halka[0] == halka[-1] else halka)
    return {"surum": SURUM, "kaynaklar": {"OpenStreetMap": 2, "Microsoft ML Buildings": 1},
            "binalar": [_b(b, e, yukseklik=7.0),                  # parselde
                        _b(b + 0.002, e, yukseklik=21.0),         # komşu, height
                        _b(b - 0.002, e, kat=4),                  # komşu, num_floors
                        _b(b, e + 0.002)]}                        # bilgisiz


Z, TX, TY = 14, 9511, 6140


# --------------------------------------------------------------------------- #
class Bicimler(unittest.TestCase):
    def test_hilbert_karo_numarasi_belirtimdeki_ornekler(self):
        self.assertEqual(bina.karo_no(0, 0, 0), 0)
        self.assertEqual([bina.karo_no(1, *xy) for xy in ((0, 0), (0, 1), (1, 1), (1, 0))], [1, 2, 3, 4])
        self.assertEqual(bina.karo_no(2, 0, 0), 5)
        self.assertEqual(bina.karo_no(3, 0, 0), 21)

    def test_dizin_gidis_donus_ve_ardisik_ofset(self):
        kayit = [(5, 1, 100, 0), (6, 1, 50, 100), (9, 2, 10, 400), (20, 0, 33, 7)]
        self.assertEqual(bina.dizin_coz(dizin(kayit)), kayit)

    def test_mvt_dis_halka_ve_delik(self):
        dis, delik = kare(2000, 2000, 400), kare_ters(2000, 2000, 100)
        ham = karo_uret([({"id": "a", "height": 12.5, "is_underground": False}, [dis, delik]),
                         ({"id": "b", "num_floors": 4}, [kare(500, 500, 50)])])
        olcek, nesneler = bina.mvt_coz(ham)
        self.assertEqual(olcek, 4096)
        self.assertEqual(len(nesneler), 2)
        self.assertEqual(nesneler[0]["oz"], {"id": "a", "height": 12.5, "is_underground": False})
        (cokgen,) = bina._cokgenlere_ayir(nesneler[0]["halkalar"])
        self.assertEqual(len(cokgen), 2)                                  # dış + delik
        self.assertGreater(bina._alan(cokgen[0]), 0)
        self.assertLess(bina._alan(cokgen[1]), 0)
        self.assertEqual(nesneler[1]["oz"]["num_floors"], 4)
        self.assertEqual(bina.mvt_coz(ham, "yok")[1], [])

    def test_bozuk_veri_bina_hatasi_olur(self):
        for bozuk in (b"\x80", b"\x1a\x05ab", b"\x0f"):
            with self.assertRaises(bina.BinaHatasi):
                bina.mvt_coz(bozuk)
        with self.assertRaises(bina.BinaHatasi):
            bina.baslik_coz(b"PMTiles" + b"\x02" + b"\x00" * 119)


class Binalar(unittest.TestCase):
    def setUp(self):
        bina.onbellegi_bosalt()
        os.environ.pop("TKGM_OVERTURE_SURUM", None)
        self.kutu = karo_sinirlari(Z, TX, TY)

    def _arsiv(self, yaprakli=False):
        sol = karo_uret([
            ({"id": "ortak", "@geometry_source": "OpenStreetMap", "height": 9.0}, [kare(4000, 2000, 150)]),
            ({"id": "tek", "@geometry_source": "Microsoft ML Buildings"}, [kare(1000, 1000, 60)]),
            ({"id": "yeralti", "is_underground": True}, [kare(1500, 1500, 60)])])
        # Aynı bina komşu karoda kırpılmış küçük parçayla da bulunur.
        sag = karo_uret([({"id": "ortak", "@geometry_source": "OpenStreetMap", "height": 9.0},
                          [kare(20, 2000, 40)])])
        return arsiv_uret({(Z, TX, TY): sol, (Z, TX + 1, TY): sag}, yaprakli)

    def test_kutudaki_binalar_tekillesir_yeralti_atilir(self):
        for yaprakli in (False, True):
            bina.onbellegi_bosalt()
            gunluk = []
            b0, g0, b1, k0 = self.kutu
            sag = karo_sinirlari(Z, TX + 1, TY)
            sonuc = bina.binalar(b0 + 1e-6, g0 + 1e-6, sag[2] - 1e-6, k0 - 1e-6,
                                 al=getirici(self._arsiv(yaprakli), gunluk))
            self.assertEqual(sonuc["surum"], SURUM)
            self.assertEqual(sonuc["karo"], 2)
            kaynaklar = sorted(b["kaynak"] for b in sonuc["binalar"])
            self.assertEqual(kaynaklar, ["Microsoft ML Buildings", "OpenStreetMap"])
            ortak = next(b for b in sonuc["binalar"] if b["kaynak"] == "OpenStreetMap")
            self.assertEqual(ortak["yukseklik_m"], 9.0)
            # Büyük parça (sol karo) kaldı: dört köşe, merkez sol karonun içinde.
            self.assertEqual(len(ortak["cokgenler"][0][0]), 4)
            self.assertLess(ortak["merkez"][0], sag[0])
            self.assertTrue(any(u == bina.KATALOG for u, _, _ in gunluk))
            self.assertTrue(all(u in (bina.KATALOG, URL) for u, _, _ in gunluk))

    def test_merkezi_kutu_disindaki_bina_gelmez(self):
        b0, g0, b1, k0 = self.kutu
        orta_b = (b0 + b1) / 2.0
        sonuc = bina.binalar(b0, g0, orta_b, k0, al=getirici(self._arsiv()))
        self.assertEqual([b["kaynak"] for b in sonuc["binalar"]], ["Microsoft ML Buildings"])

    def test_buyuk_kutu_reddedilir(self):
        b0, g0, b1, k0 = self.kutu
        with self.assertRaises(bina.BinaHatasi):
            bina.binalar(b0 - 0.1, g0 - 0.1, b1 + 0.1, k0 + 0.1, al=getirici(self._arsiv()))

    def test_katalog_ve_surum_sabiti(self):
        with self.assertRaises(bina.BinaHatasi):
            bina.guncel_surum(getirici(b"", katalog=b'{"latest": "../../etc"}'))
        os.environ["TKGM_OVERTURE_SURUM"] = "2026-07-22.0"
        try:
            self.assertEqual(bina.guncel_surum(), "2026-07-22.0")    # ağa çıkmaz
            os.environ["TKGM_OVERTURE_SURUM"] = "latest; rm -rf"
            with self.assertRaises(bina.BinaHatasi):
                bina.guncel_surum()
        finally:
            os.environ.pop("TKGM_OVERTURE_SURUM", None)

    def test_kaldirilmis_surum_katalogu_yeniden_okur(self):
        eski = json.dumps({"latest": "2026-06-01.0"}).encode()
        yeni = json.dumps({"latest": SURUM}).encode()
        sira = [eski, yeni]
        arsiv = self._arsiv()

        def al(url, bas, uzunluk):
            if url == bina.KATALOG:
                return sira.pop(0)
            return getirici(arsiv)(url, bas, uzunluk)
        b0, g0, b1, k0 = self.kutu
        sonuc = bina.binalar(b0, g0, b1, k0, al=al)
        self.assertEqual(sonuc["surum"], SURUM)

    def test_izinsiz_adres_aga_cikmadan_reddedilir(self):
        for url in ("https://example.com/x.pmtiles", "https://tkgm.gov.tr/", bina.ARSIV_KOKU[:-1] + "x/"):
            with self.assertRaises(bina.BinaHatasi):
                bina._http(url, 0, 10)


class KatCozumle(unittest.TestCase):
    def test_nitelikten_kat(self):
        ornekler = {
            "6 Katlı Betonarme Apartman": 6, "Altı Katlı Kargir Apartman": 6, "ALTI KATLI KARGİR": 6,
            "Tek Katlı Kargir Ev ve Arsası": 1, "İki katlı ahşap ev": 2, "On iki katlı betonarme": 12,
            "Onbir Katlı Bina": 11, "Zemin+4 Katlı Apartman": 5, "Zemin Kat + 3 Katlı": 4,
            "Bodrum + Zemin + 3 Normal Kat Mesken": 4, "5 normal katlı apartman": 5,
            "Betonarme 7 kat": 7,
        }
        for metin, beklenen in ornekler.items():
            with self.subTest(metin=metin):
                cozum = bina.kat_cozumle(metin)
                self.assertIsNotNone(cozum)
                self.assertEqual(cozum[0], beklenen)

    def test_kat_gecmeyen_nitelik_tahmin_edilmez(self):
        for metin in ("Betonarme Apartman", "Arsa", "Tarla", "Kargir Ev", "", None, 12,
                      "Çok Katlı Betonarme", "Katlı otopark", "Altında dükkan olan ev"):
            with self.subTest(metin=metin):
                self.assertIsNone(bina.kat_cozumle(metin))


class KutleModeli(unittest.TestCase):
    def setUp(self):
        (self.p,) = parseller.oku(ornek_geojson())
        self.veri = ornek_bina_verisi(self.p)

    def test_acik_veri_kipi(self):
        m = bina.kutle_modeli(self.p, self.veri)
        self.assertEqual(m["ozet"]["kip"], "acik_veri")
        self.assertEqual([(b["parselde"], b["h"], b["dayanak"]) for b in m["binalar"]],
                         [(True, 7.0, "veri"), (False, 21.0, "veri"), (False, 12.0, "veri"),
                          (False, 9.0, "varsayim")])
        self.assertTrue(m["ozet"]["notlar"][0].startswith("TAHMİNİDİR"))
        self.assertIn("Microsoft ML Buildings (CDLA-Permissive-2.0)", m["ozet"]["atif"])
        self.assertIn("© OpenStreetMap", m["ozet"]["atif"])

    def test_nitelik_ve_kullanici_kati_acik_veri_yuksekligini_karistirmaz(self):
        p = copy.deepcopy(self.p)
        p["oznitelik"]["nitelik"] = "Altı Katlı Betonarme Apartman"
        for model, kat, dayanak in ((bina.kutle_modeli(p, self.veri), 6, "nitelik"),
                                    (bina.kutle_modeli(self.p, self.veri, kat=2), 2, "kullanici")):
            self.assertEqual(model["ozet"]["kip"], "kullanici")
            self.assertEqual(model["binalar"][0]["h"], kat * 3.0)
            self.assertEqual(model["binalar"][0]["dayanak"], dayanak)
            # Komşunun OSM yüksekliği (21 m) ve kat sayısı (4) bu kipte kullanılmaz.
            self.assertEqual({b["h"] for b in model["binalar"][1:]}, {9.0})
            self.assertEqual(model["ozet"]["parsel_binasi"]["kat"], kat)

    def test_parselde_iz_yoksa_not_duser(self):
        veri = dict(self.veri, binalar=self.veri["binalar"][1:])
        m = bina.kutle_modeli(self.p, veri, kat=5)
        self.assertTrue(any("bina izi yok" in n for n in m["ozet"]["notlar"]))

    def test_gecersiz_kat(self):
        for kat in ("beş", 0, 500, "1; DROP"):
            with self.assertRaises(bina.BinaHatasi):
                bina.kutle_modeli(self.p, self.veri, kat=kat)


class UcBoyutSahnesi(unittest.TestCase):
    def setUp(self):
        (self.p,) = parseller.oku(ornek_geojson())
        self.izgara = _duz_izgara(self.p)
        self.ozet = h.arazi_ozeti(self.p, self.izgara)
        self.kutle = bina.kutle_modeli(self.p, ornek_bina_verisi(self.p))

    @staticmethod
    def _veri(sayfa):
        govde = sayfa[sayfa.index("const V=") + len("const V="):]
        return json.JSONDecoder().raw_decode(govde)[0]

    def test_binalar_sahneye_girer(self):
        sayfa = h.arazi_3d_html(self.p, [], self.izgara, self.ozet, self.kutle)
        veri = self._veri(sayfa)
        self.assertEqual([b["t"] for b in veri["binalar"]], ["p", "v", "v", "t"])
        self.assertEqual(veri["pb"], 7.0)
        p = veri["binalar"][0]
        self.assertEqual(len(p["c"][0][0]), 8)                             # 4 köşe × (x, -güney)
        # 0,0001° kare: doğu-batı 0,0001·mb, kuzey-güney 0,0001·me metre (±0,1 m yuvarlama).
        mb, me = h.metre_derece(*reversed(ornek_bina_verisi(self.p)["binalar"][0]["merkez"]))
        xs, ys = p["c"][0][0][0::2], p["c"][0][0][1::2]
        self.assertAlmostEqual(max(xs) - min(xs), 0.0001 * mb, delta=0.2)
        self.assertAlmostEqual(max(ys) - min(ys), 0.0001 * me, delta=0.2)
        for b in veri["binalar"]:
            self.assertLessEqual(b["y"], max(veri["dz"]) / 10.0)
        self.assertIn('id="binalar"', sayfa)
        self.assertIn("Binalar (tahmini kütle)", sayfa)
        self.assertIn("Parseldeki bina (tahmini)", sayfa)
        self.assertIn("Overture Maps Foundation (%s)" % SURUM, sayfa)

    def test_uyari_her_zaman_gorunur_ve_kartin_disinda(self):
        for kutle in (None, self.kutle):
            sayfa = h.arazi_3d_html(self.p, [], self.izgara, self.ozet, kutle)
            self.assertIn(h._h(h.UYARI_3D), sayfa)
            bas = sayfa.index('<div class="uyari3d"')
            self.assertGreater(bas, sayfa.index("</details></div>"))
            self.assertLess(bas, sayfa.index("<script"))
        self.assertIn("gerçek (ölçülmüş ya da resmî) veriye dayanmaz", h.UYARI_3D)
        self.assertIn("Yapay zekâ", h.UYARI_3D)
        self.assertIn("fikir vermek", h.UYARI_3D)

    def test_atif_kartin_disinda_hep_gorunur(self):
        """Kart katlanınca Esri/ODbL atfı ekrandan kalkmamalı: atıf şeridi kartın dışındadır."""
        sayfa = h.arazi_3d_html(self.p, [], self.izgara, self.ozet, self.kutle)
        atif = sayfa.index('<div class="atif3d">')
        self.assertGreater(atif, sayfa.index("</details></div>"))
        satir = sayfa[atif:sayfa.index("</div>", atif)]
        for beklenen in ("Copernicus", "© OpenStreetMap", "Overture Maps Foundation", 'id="atif-uydu" hidden',
                         "Powered by Esri"):
            self.assertIn(beklenen, satir)
        self.assertNotIn("Overture", h.arazi_3d_html(self.p, [], self.izgara, self.ozet)[atif:atif + 400]
                         .split("</div>")[0])

    def test_esri_anahtari_yalniz_eklenince_girer(self):
        anahtar = "AAPTxy" + "A1b2_C3-d4.e5" * 4
        for sayfa in (h.harita_html(self.p), h.arazi_3d_html(self.p, [], self.izgara, self.ozet, self.kutle)):
            self.assertEqual(sayfa.count(h.ESRI_ISARETI), 1)
            self.assertNotIn(anahtar, sayfa)
            ekli = h.esri_anahtari_ekle(sayfa, anahtar)
            self.assertIn('ESRI="%s"' % anahtar, ekli)
            self.assertNotIn(h.ESRI_ISARETI, ekli)
            for kotu in (None, "", "kısa", "a" * 20 + "</script>", "a" * 20 + '"+alert(1)+"'):
                self.assertEqual(h.esri_anahtari_ekle(sayfa, kotu), sayfa)

    def test_parsel_verisi_isareti_taklit_edemez(self):
        gj = json.loads(ornek_geojson())
        gj["features"][0]["properties"]["nitelik"] = h.ESRI_ISARETI + " Altı Katlı"
        (kotu,) = parseller.oku(json.dumps(gj))
        for sayfa in (h.harita_html(kotu), h.arazi_3d_html(kotu, [], self.izgara, self.ozet, self.kutle)):
            self.assertEqual(sayfa.count(h.ESRI_ISARETI), 1)


def _duz_izgara(p, n=17):
    b0, e0, b1, e1 = h.parsel_kutusu(p, None, 300.0)
    return {"enlemler": [e1 - (e1 - e0) * i / (n - 1) for i in range(n)],
            "boylamlar": [b0 + (b1 - b0) * j / (n - 1) for j in range(n)],
            "z": [[900.0 + j * 0.5 for j in range(n)] for _ in range(n)], "kaynak": "sentetik"}


@unittest.skipUnless(os.environ.get("TKGM_CANLI_TEST") == "1", "canlı: TKGM_CANLI_TEST=1")
class Canli(unittest.TestCase):
    def test_istanbul_binalari(self):
        bina.onbellegi_bosalt()
        e, b, r = 41.0432, 28.9993, 150.0
        de, db = r / 111320.0, r / (111320.0 * 0.7547)
        sonuc = bina.binalar(b - db, e - de, b + db, e + de)
        print("\nCANLI Overture %s: %d bina, %d karo, %d bayt, kaynaklar %s"
              % (sonuc["surum"], len(sonuc["binalar"]), sonuc["karo"], sonuc["indirilen_bayt"],
                 sonuc["kaynaklar"]))
        self.assertGreater(len(sonuc["binalar"]), 50)
        self.assertLess(sonuc["indirilen_bayt"], 2_000_000)


if __name__ == "__main__":
    unittest.main()
