"""Ağsız testler: parsel föyü (.docx) ve portföy (.xlsx).

Bu testler paketin KURALLARA uyduğunu sınar (geçerli zip, ayrışan XML, çözülen
ilişkiler, gerçek sayı hücreleri, çözülebilen PNG). Word'ün ve Excel'in dosyayı
onarım istemeden açtığını sınayamazlar; o, gerçek uygulamayla elle doğrulanır.
"""

import io
import json
import math
import os
import re
import struct
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile
import zlib

_BURASI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_BURASI))
sys.path.insert(0, _BURASI)   # test_offline: `unittest discover -s tests` dışındaki çalıştırmalar için

import tkgm_geo as geo  # noqa: E402
import tkgm_hukuk as hukuk  # noqa: E402
import tkgm_parsel as parseller  # noqa: E402
import tkgm_rapor as rapor  # noqa: E402
from test_offline import ornek_geojson  # noqa: E402
from tkgm_analiz import olc, tr_bicim  # noqa: E402
from tkgm_kroki import UYARI  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PAKET = "{http://schemas.openxmlformats.org/package/2006/relationships}"
TURLER = "{http://schemas.openxmlformats.org/package/2006/content-types}"
SVG = "{http://www.w3.org/2000/svg}"


def ornek_parsel(**kw):
    (p,) = parseller.oku(ornek_geojson(**kw))
    return p


def kaba_parsel():
    """Parsel Sorgu dışa aktarımı gibi: koordinatlar 5 ondalığa (~1 m) yuvarlanmış."""
    gj = json.loads(ornek_geojson())
    halka = gj["features"][0]["geometry"]["coordinates"][0]
    gj["features"][0]["geometry"]["coordinates"][0] = [[round(b, 5), round(e, 5)] for b, e in halka]
    (p,) = parseller.oku(json.dumps(gj))
    return p


def cok_koseli(n, oznitelik=None):
    """TM33'te n köşeli düzgün çokgen (yarıçap 50 m)."""
    halka = []
    for i in range(n):
        a = 2.0 * math.pi * i / n
        enlem, boylam = geo.tm_geri(520000.0 + 50.0 * math.cos(a), 4420000.0 + 50.0 * math.sin(a), 33)
        halka.append([boylam, enlem])
    halka.append(halka[0])
    ozn = {"ilAd": "İzmir", "ilceAd": "Çeşme", "mahalleAd": "Ilıca", "adaNo": "7", "parselNo": "12",
           "nitelik": "Zeytinlik"}
    ozn.update(oznitelik or {})
    (p,) = parseller.oku(json.dumps({"type": "Feature", "properties": ozn,
                                     "geometry": {"type": "Polygon", "coordinates": [halka]}}))
    return p


def paketi_dogrula(test, veri):
    """Zip sağlam mı, her XML parçası ayrışıyor mu, her iç ilişki var olan bir parçayı gösteriyor mu,
    her parçanın içerik türü tanımlı mı. {ad: bayt} döndürür."""
    z = zipfile.ZipFile(io.BytesIO(veri))
    test.assertIsNone(z.testzip())
    parcalar = {ad: z.read(ad) for ad in z.namelist()}
    for ad, govde in parcalar.items():
        if ad.endswith((".xml", ".rels", ".svg")):
            ET.fromstring(govde)     # ayrışmazsa ParseError ile düşer
            test.assertTrue(govde.startswith(b"<?xml") or ad.endswith(".svg"), ad)
    for ad, govde in parcalar.items():
        if not ad.endswith(".rels"):
            continue
        # "word/_rels/document.xml.rels" → hedefler "word/" köküne göredir
        kok = os.path.dirname(os.path.dirname(ad))
        for iliski in ET.fromstring(govde).iter(PAKET + "Relationship"):
            if iliski.get("TargetMode") != "External":
                hedef = "/".join(x for x in (kok, iliski.get("Target")) if x)
                test.assertIn(hedef, parcalar, "%s → %s" % (ad, hedef))
    turler = ET.fromstring(parcalar["[Content_Types].xml"])
    uzantilar = {t.get("Extension") for t in turler.iter(TURLER + "Default")}
    ezilenler = {t.get("PartName") for t in turler.iter(TURLER + "Override")}
    for ad in parcalar:
        if ad != "[Content_Types].xml":
            test.assertTrue("/" + ad in ezilenler or ad.rsplit(".", 1)[-1] in uzantilar, ad)
    return parcalar


def png_coz(veri):
    """(genişlik, yükseklik, satırlar) — CRC ve süzgeç baytı denetlenerek. Yalnız 8 bit RGB."""
    assert veri[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat, baslik = 8, b"", None
    while i < len(veri):
        boy, tur = struct.unpack(">I4s", veri[i:i + 8])
        govde = veri[i + 8:i + 8 + boy]
        (crc,) = struct.unpack(">I", veri[i + 8 + boy:i + 12 + boy])
        assert crc == zlib.crc32(tur + govde) & 0xFFFFFFFF, tur
        if tur == b"IHDR":
            baslik = struct.unpack(">IIBBBBB", govde)
        elif tur == b"IDAT":
            idat += govde
        i += 12 + boy
    gen, yuk = baslik[0], baslik[1]
    assert baslik[2:] == (8, 2, 0, 0, 0), baslik
    ham = zlib.decompress(idat)
    assert len(ham) == yuk * (1 + 3 * gen)
    satirlar = []
    for y in range(yuk):
        satir = ham[y * (1 + 3 * gen):(y + 1) * (1 + 3 * gen)]
        assert satir[0] == 0          # süzgeç türü "yok": baytlar doğrudan pikseldir
        satirlar.append(satir[1:])
    return gen, yuk, satirlar


def belge_metni(parcalar, ad="word/document.xml"):
    return [t.text or "" for t in ET.fromstring(parcalar[ad]).iter(W + "t")]


class Foy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsel = ornek_parsel()
        cls.veri = rapor.foy_docx(cls.parsel, tarih="2026-09-20")

    def test_paket_gecerli_ve_parcalar_tam(self):
        parcalar = paketi_dogrula(self, self.veri)
        for ad in ("[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml",
                   "word/_rels/document.xml.rels", "word/media/kroki.png", "word/media/kroki.svg"):
            self.assertIn(ad, parcalar)
        turler = {t.get("Extension"): t.get("ContentType")
                  for t in ET.fromstring(parcalar["[Content_Types].xml"]).iter(TURLER + "Default")}
        self.assertEqual(turler["png"], "image/png")
        self.assertEqual(turler["svg"], "image/svg+xml")

    def test_etiket_alan_ve_turkce_karakterler(self):
        parcalar = paketi_dogrula(self, self.veri)
        ham = parcalar["word/document.xml"].decode("utf-8")
        self.assertIn("Ankara/Çankaya/Örnek 123/4", ham)
        self.assertIn("1.200,00", ham)
        metin = belge_metni(parcalar)
        for beklenen in ("PARSEL FÖYÜ", "Ankara/Çankaya/Örnek 123/4", "Çankaya", "İlçe", "Köşe sayısı",
                         "Ölçüler (hesap)", "1.200,00 m²", "140,00 m", "ITRF96 TM 3° DOM 33 (EPSG:5255)",
                         "Düzenleme: 2026-09-20 — ArthurLegal tkgm"):
            self.assertIn(beklenen, metin)
        for satir in UYARI:
            self.assertIn(satir, metin)
        self.assertEqual(metin[-1], "Düzenleme: 2026-09-20 — ArthurLegal tkgm")
        self.assertTrue(any(UYARI[0] in t for t in belge_metni(parcalar, "word/footer1.xml")))

    def test_sayfa_a4_ve_kenar_bosluklari_2cm(self):
        kok = ET.fromstring(paketi_dogrula(self, self.veri)["word/document.xml"])
        boyut, bosluk = next(kok.iter(W + "pgSz")), next(kok.iter(W + "pgMar"))
        self.assertEqual((boyut.get(W + "w"), boyut.get(W + "h")), ("11906", "16838"))
        for kenar in ("top", "right", "bottom", "left"):
            self.assertEqual(bosluk.get(W + kenar), "1134")      # 2 cm = 1134 twip
        for tablo in kok.iter(W + "tbl"):                        # hiçbir tablo yazı alanından taşmaz
            self.assertLessEqual(sum(int(g.get(W + "w")) for g in tablo.iter(W + "gridCol")), 9638)

    def test_gorsel_png_yedegi_ve_svg_uzantisi(self):
        parcalar = paketi_dogrula(self, self.veri)
        kok = ET.fromstring(parcalar["word/document.xml"])
        iliskiler = {i.get("Id"): i.get("Target")
                     for i in ET.fromstring(parcalar["word/_rels/document.xml.rels"])}
        blip = next(kok.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip"))
        svg_blip = next(kok.iter("{http://schemas.microsoft.com/office/drawing/2016/SVG/main}svgBlip"))
        self.assertEqual(iliskiler[blip.get(R + "embed")], "media/kroki.png")     # yedek asıl blip'tir
        self.assertEqual(iliskiler[svg_blip.get(R + "embed")], "media/kroki.svg")
        # Görsel A4 kroki sayfası değil, parsel çizimidir: yazı alanına (17 cm) sığar.
        olcu = next(kok.iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent"))
        self.assertLessEqual(int(olcu.get("cx")), 170 * 36000)
        self.assertLessEqual(int(olcu.get("cy")), 110 * 36000)

    def test_png_cozulur_ve_parseli_gosterir(self):
        png = paketi_dogrula(self, self.veri)["word/media/kroki.png"]
        self.assertGreater(len(png), 5000)
        gen, yuk, satirlar = png_coz(png)
        self.assertEqual(max(gen, yuk), 1400)
        # Parselin ortası etiketin (koyu rakam, beyaz hale) altında kalabilir; dolgu rengi bu
        # yüzden orta satır boyunca aranır. Alt örnekleme rengi birkaç düzey kaydırır.
        orta_satir = satirlar[yuk // 2]
        dolgulu = [i for i in range(0, 3 * gen, 3)
                   if all(abs(orta_satir[i + c] - hedef) <= 8 for c, hedef in enumerate((0xFF, 0xF3, 0xD6)))]
        self.assertGreater(len(dolgulu), gen // 4)
        koyu = sum(1 for s in satirlar[::7] for i in range(0, len(s), 3 * 5) if s[i] < 60)
        self.assertGreater(koyu, 100)       # kalın sınır çizgisi
        self.assertEqual(satirlar[5][3 * (gen // 2):3 * (gen // 2) + 3], b"\xff\xff\xff")   # pay beyaz

    def test_svg_kenar_boylari_ve_etiket(self):
        svg = paketi_dogrula(self, self.veri)["word/media/kroki.svg"]
        kok = ET.fromstring(svg)
        metinler = [t.text for t in kok.iter(SVG + "text")]
        for beklenen in ("123/4", "40,00", "30,00", "1.200,00 m²", "1", "4"):
            self.assertIn(beklenen, metinler)
        gen, yuk = [float(x) for x in kok.get("viewBox").split()[2:]]
        self.assertLess(yuk, 297.0)         # A4 kroki sayfası gömülmedi
        self.assertNotIn(b"paint-order", svg)
        self.assertNotIn(b"clip-path", svg)
        # Ölçekli: 40 m'lik kenar 1/500'de 80 mm.
        yol = [p for p in kok.iter(SVG + "path") if p.get("fill") == "#fff3d6"][0]
        nokta = [tuple(map(float, t.strip("ML ").split(",")))
                 for t in yol.get("d").rstrip(" Z").split(" L")]
        self.assertAlmostEqual(math.dist(nokta[0], nokta[1]), 80.0, delta=0.05)
        self.assertIn("Ölçek 1/500", " ".join(belge_metni(paketi_dogrula(self, self.veri))))

    def test_kenar_tablosu(self):
        metin = belge_metni(paketi_dogrula(self, self.veri))
        i = metin.index("1-2")
        self.assertEqual(metin[i:i + 3], ["1-2", "40,00", "77,78"])
        self.assertIn("4-1", metin)
        self.assertFalse(any("kenar gösterildi" in t for t in metin))

    def test_kenar_tablosu_40_satirda_kesilir(self):
        metin = belge_metni(paketi_dogrula(self, rapor.foy_docx(cok_koseli(50))))
        kenarlar = [t for t in metin if re.fullmatch(r"\d+-\d+", t)]
        self.assertEqual(len(kenarlar), 40)
        # Kenarlar yan yana üç bloğa dizilir; belge sırası satır satırdır, o yüzden küme karşılaştırılır.
        self.assertEqual(set(kenarlar), {"%d-%d" % (i, i + 1) for i in range(1, 41)})
        self.assertEqual(kenarlar[:3], ["1-2", "15-16", "29-30"])
        self.assertTrue(any("İlk 40 kenar gösterildi (toplam 50)" in t for t in metin))
        self.assertIn("İzmir/Çeşme/Ilıca 7/12", metin)

    def test_cok_koseli_parselde_yazi_yok_ama_belge_gecerli(self):
        parcalar = paketi_dogrula(self, rapor.foy_docx(cok_koseli(75)))
        self.assertTrue(any("köşe sayısı 60" in t for t in belge_metni(parcalar)))
        png_coz(parcalar["word/media/kroki.png"])

    def test_hukuki_notlar(self):
        metin = belge_metni(paketi_dogrula(self, rapor.foy_docx(self.parsel, konu="onalim")))
        tam = " ".join(metin)
        self.assertIn("5403", tam)                                   # Tarla → rejim uyarısı
        k = hukuk.kopru("onalim")
        self.assertIn("Konu: %s" % k["baslik"], metin)
        self.assertIn(k["uyari"], metin)
        self.assertIn(k["sure"], metin)
        self.assertIn(k["teyit"], metin)
        self.assertTrue(any(t.startswith("•  4721 sayılı Türk Medeni Kanunu — md. 732") for t in metin))
        self.assertIn(hukuk.DOGRULAMA, metin)
        self.assertLess(metin.index(k["teyit"]), metin.index(hukuk.DOGRULAMA))
        # Konusuz föyde köprü yok, doğrulama notu yine var.
        konusuz = belge_metni(paketi_dogrula(self, self.veri))
        self.assertIn(hukuk.DOGRULAMA, konusuz)
        self.assertFalse(any(t.startswith("Konu:") for t in konusuz))
        with self.assertRaises(ValueError):
            rapor.foy_docx(self.parsel, konu="olmayan_konu")

    def test_rejimsiz_nitelik_sessiz_kalmaz(self):
        (p,) = parseller.oku(ornek_geojson())
        p["oznitelik"]["nitelik"] = "Bağımsız bölüm"
        metin = " ".join(belge_metni(paketi_dogrula(self, rapor.foy_docx(p))))
        self.assertIn("kısıt bulunmadığı anlamına gelmez", metin)

    def test_komsular_cizilir_ve_kendisi_komsu_sayilmaz(self):
        gj = json.loads(ornek_geojson(genislik=35.0))
        gj["features"][0]["properties"].update({"parselNo": "5", "alan": "1.050,00"})
        # Komşu: aynı dikdörtgenin 40 m doğusu (yerel eksende); kaba bir öteleme yeterli.
        for nokta in gj["features"][0]["geometry"]["coordinates"][0]:
            nokta[0] += 0.00045
        (komsu,) = parseller.oku(json.dumps(gj))
        parcalar = paketi_dogrula(self, rapor.foy_docx(self.parsel, [komsu, self.parsel]))
        metinler = [t.text for t in ET.fromstring(parcalar["word/media/kroki.svg"]).iter(SVG + "text")]
        self.assertIn("123/5", metinler)
        self.assertTrue(any("gri: 1 komşu parsel" in t for t in belge_metni(parcalar)))
        self.assertNotEqual(parcalar["word/media/kroki.png"],
                            paketi_dogrula(self, self.veri)["word/media/kroki.png"])

    def test_xml_kacisi_ve_gecersiz_karakterler(self):
        (p,) = parseller.oku(ornek_geojson())
        p["oznitelik"]["mahalle"] = 'A&B <Yeni> "Mah."\x0b'
        p["oznitelik"]["ada"] = "12/A"            # rakam olmayan ada: raster etiketi atlanır, belge bozulmaz
        parcalar = paketi_dogrula(self, rapor.foy_docx(p))
        self.assertIn('A&B <Yeni> "Mah."', belge_metni(parcalar))
        self.assertIn("12/A/4", [t.text for t in ET.fromstring(parcalar["word/media/kroki.svg"]).iter(SVG + "text")])

    def test_ic_halkali_ve_cok_parcali_parsel(self):
        gj = json.loads(ornek_geojson())
        dis = gj["features"][0]["geometry"]["coordinates"][0]
        mb, me = sum(p[0] for p in dis[:4]) / 4, sum(p[1] for p in dis[:4]) / 4
        ic = [[mb + (p[0] - mb) * 0.4, me + (p[1] - me) * 0.4] for p in dis]
        ikinci = [[p[0] + 0.0008, p[1]] for p in dis]
        gj["features"][0]["geometry"] = {"type": "MultiPolygon", "coordinates": [[dis, ic], [ikinci]]}
        (p,) = parseller.oku(json.dumps(gj))
        metin = belge_metni(paketi_dogrula(self, rapor.foy_docx(p)))
        self.assertIn("Parça sayısı", metin)
        self.assertIn("5-6", metin)               # ikinci parçanın köşeleri numaralamayı sürdürür
        self.assertIn("8-5", metin)

    def test_kaba_koordinatta_sahte_kesinlik_yazilmaz(self):
        """Koordinatları ~1 m'ye yuvarlanmış dosyada föy, kroki ile aynı kuralı izler: boylar "≈" ve
        tek ondalık, belirsizlik ölçü tablosunda, farkın yorumu `olc`tan."""
        p = kaba_parsel()
        olcu = olc(p)
        self.assertIn("kenar_belirsizligi_m", olcu)      # önkoşul: `olc` dosyayı kaba buldu
        parcalar = paketi_dogrula(self, rapor.foy_docx(p))
        metin = belge_metni(parcalar)
        tam = " ".join(metin)
        for beklenen in ("Koordinat ızgarası", "Alan belirsizliği", "Kenar belirsizliği"):
            self.assertIn(beklenen, metin)
        self.assertIn("±%s m² (%%95)" % tr_bicim(olcu["alan_belirsizligi_m2"], 1), metin)
        self.assertIn(olcu["fark_yorumu"], tam)
        self.assertIn(olcu["hassasiyet_notu"], tam)
        self.assertIn("boylar ve semtler yaklaşıktır", tam)
        self.assertTrue(metin[metin.index("Hesap alanı") + 1].startswith("≈"))
        i = metin.index("1-2")
        self.assertTrue(re.fullmatch(r"\d+,\d", metin[i + 1]), metin[i + 1])     # tek ondalık
        svg = [t.text for t in ET.fromstring(parcalar["word/media/kroki.svg"]).iter(SVG + "text")]
        self.assertTrue(any(re.fullmatch(r"≈\d+,\d", t) for t in svg), svg)
        self.assertIn("1.200,00 m²", svg)                # etikette dosyadaki kayıtlı alan durur
        # Hassas dosyada bunların hiçbiri yoktur.
        hassas = " ".join(belge_metni(paketi_dogrula(self, self.veri)))
        self.assertNotIn("≈", hassas)
        self.assertNotIn("Koordinat ızgarası", hassas)

    def test_ayni_girdi_ayni_baytlar(self):
        self.assertEqual(self.veri, rapor.foy_docx(ornek_parsel(), tarih="2026-09-20"))
        self.assertNotEqual(self.veri, rapor.foy_docx(ornek_parsel(), tarih="2026-09-21"))


class Portfoy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parseller = [ornek_parsel(), ornek_parsel(genislik=50.0, alan_yazisi=""), cok_koseli(12)]
        cls.veri = rapor.portfoy_xlsx(cls.parseller, "2026-09-20")

    def _sayfa(self, no=1, veri=None):
        parcalar = paketi_dogrula(self, veri or self.veri)
        kok = ET.fromstring(parcalar["xl/worksheets/sheet%d.xml" % no])
        hucreler = {}
        for c in kok.iter(S + "c"):
            v, metin = c.find(S + "v"), c.find(S + "is/" + S + "t")
            hucreler[c.get("r")] = {"tur": c.get("t"), "v": None if v is None else v.text,
                                    "metin": None if metin is None else metin.text,
                                    "f": c.findtext(S + "f")}
        return kok, hucreler

    def test_paket_ve_sayfa_adlari(self):
        parcalar = paketi_dogrula(self, self.veri)
        kitap = ET.fromstring(parcalar["xl/workbook.xml"])
        self.assertEqual([s.get("name") for s in kitap.iter(S + "sheet")], ["Portföy", "Notlar"])

    def test_baslik_satiri(self):
        kok, h = self._sayfa()
        basliklar = [h["%s1" % rapor._sutun_adi(i)]["metin"] for i in range(len(rapor._SUTUNLAR))]
        # İlk 19 sütun görev tanımındaki sıradır; belirsizlik ve fark yorumu SONA eklenir.
        self.assertEqual(basliklar, [
            "İl", "İlçe", "Mahalle", "Ada", "Parsel", "Nitelik", "Mevkii", "Pafta", "Dosyadaki alan m²",
            "Hesap alanı m²", "Fark m²", "Fark %", "Çevre m", "Köşe", "Merkez enlem", "Merkez boylam",
            "Rejim uyarıları", "Parsel Sorgu bağlantısı", "ref",
            "Alan belirsizliği ± m² (%95)", "Fark yorumu"])
        bolme = next(kok.iter(S + "pane"))
        self.assertEqual((bolme.get("state"), bolme.get("ySplit"), bolme.get("topLeftCell")),
                         ("frozen", "1", "A2"))
        # Başlık stili kalın yazı tipine bağlı.
        stiller = ET.fromstring(paketi_dogrula(self, self.veri)["xl/styles.xml"])
        xf = stiller.find(S + "cellXfs")[rapor._S_BASLIK]
        yazi = stiller.find(S + "fonts")[int(xf.get("fontId"))]
        self.assertIsNotNone(yazi.find(S + "b"))
        self.assertEqual(len(list(kok.iter(S + "col"))), len(basliklar))

    def test_sayilar_gercek_sayi_hucresidir(self):
        _, h = self._sayfa()
        for konum in ("I2", "J2", "K2", "L2", "M2", "N2", "O2", "P2", "D2", "E2"):
            self.assertIn(h[konum]["tur"], (None, "n"), konum)
            float(h[konum]["v"])
        self.assertEqual(float(h["I2"]["v"]), 1200.0)
        self.assertAlmostEqual(float(h["J2"]["v"]), 1200.0, delta=0.01)
        self.assertAlmostEqual(float(h["M2"]["v"]), 140.0, delta=0.01)
        self.assertEqual((h["D2"]["v"], h["E2"]["v"], h["N2"]["v"]), ("123", "4", "4"))
        # Dosyada alan yoksa hücre YOKTUR (0 değil): fark da hesaplanmaz.
        for konum in ("I3", "K3", "L3"):
            self.assertNotIn(konum, h)
        self.assertAlmostEqual(float(h["J3"]["v"]), 1500.0, delta=0.01)

    def test_metinler_ve_turkce_karakterler(self):
        _, h = self._sayfa()
        self.assertEqual([h[k]["metin"] for k in ("A2", "B2", "C2", "F2", "H2")],
                         ["Ankara", "Çankaya", "Örnek", "Tarla", "I29"])
        self.assertEqual([h[k]["metin"] for k in ("A4", "B4", "C4")], ["İzmir", "Çeşme", "Ilıca"])
        self.assertEqual(h["A2"]["tur"], "inlineStr")
        self.assertIn("5403", h["Q2"]["metin"])
        self.assertIn("3573", h["Q4"]["metin"])
        self.assertEqual(h["S2"]["metin"], self.parseller[0]["ref"])
        self.assertTrue(re.fullmatch(
            r"https://parselsorgu\.tkgm\.gov\.tr/#ara/cografi/39\.\d{6}/33\.\d{6}", h["R2"]["metin"]))

    def test_toplam_satiri(self):
        _, h = self._sayfa()
        self.assertEqual(h["A5"]["metin"], "TOPLAM (3 parsel)")
        self.assertEqual(h["I5"]["f"], "SUBTOTAL(109,I2:I4)")
        self.assertEqual(h["J5"]["f"], "SUBTOTAL(109,J2:J4)")
        self.assertEqual(float(h["I5"]["v"]), 1200.0)
        self.assertAlmostEqual(float(h["J5"]["v"]), sum(float(h["J%d" % i]["v"]) for i in (2, 3, 4)),
                               delta=0.011)
        self.assertEqual((h["K5"]["v"], h["K5"]["f"]), (None, None))   # yalnız iki alan sütunu toplanır

    def test_notlar_sayfasi(self):
        _, h = self._sayfa(2)
        metin = [h[k]["metin"] for k in sorted(h, key=lambda k: int(k[1:]))]
        self.assertIn("Düzenleme: 2026-09-20 — ArthurLegal tkgm", metin)
        for satir in UYARI:
            self.assertIn(satir, metin)
        self.assertIn(hukuk.DOGRULAMA, metin)

    def test_kaba_koordinatta_belirsizlik_ve_yorum_sutunlari(self):
        p = kaba_parsel()
        olcu = olc(p)
        _, h = self._sayfa(veri=rapor.portfoy_xlsx([p, ornek_parsel()]))
        self.assertIn(h["T2"]["tur"], (None, "n"))
        self.assertEqual(float(h["T2"]["v"]), olcu["alan_belirsizligi_m2"])
        self.assertEqual(h["U2"]["metin"], olcu["fark_yorumu"])
        self.assertNotIn("T3", h)                        # hassas dosyada belirsizlik hücresi yok
        self.assertNotIn("U3", h)

    def test_rakam_olmayan_ada_metin_kalir(self):
        p = ornek_parsel()
        p["oznitelik"]["ada"] = "007"
        p["oznitelik"]["parsel"] = "12/A"
        _, h = self._sayfa(veri=rapor.portfoy_xlsx([p]))
        self.assertEqual((h["D2"]["metin"], h["E2"]["metin"]), ("007", "12/A"))
        self.assertEqual(rapor._tam_sayi("0"), 0)
        self.assertEqual(rapor._sutun_adi(0) + rapor._sutun_adi(25) + rapor._sutun_adi(26), "AZAA")

    def test_bos_portfoy_gecerli_dosyadir(self):
        kok, h = self._sayfa(veri=rapor.portfoy_xlsx([]))
        self.assertEqual(h["A1"]["metin"], "İl")
        self.assertNotIn("A2", h)
        self.assertIsNone(kok.find(S + "autoFilter"))

    def test_ayni_girdi_ayni_baytlar(self):
        self.assertEqual(self.veri, rapor.portfoy_xlsx(self.parseller, "2026-09-20"))


class AgYok(unittest.TestCase):
    def test_modul_ag_kutuphanesi_almaz(self):
        """Parsel Sorgu Kullanım Koşulları md. 3: bu modülün TKGM'ye — ya da herhangi bir yere —
        istek göndermesinin yolu olmamalı. Adres yalnız dizgi olarak geçer."""
        with open(rapor.__file__, encoding="utf-8") as fh:
            kaynak = fh.read()
        ithalat = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", kaynak, re.M)
        self.assertFalse({"socket", "ssl", "http", "urllib", "ftplib", "requests", "subprocess",
                          "webbrowser", "asyncio"} & {i.split(".")[0] for i in ithalat}, ithalat)
        # Bütün ithalat modül başındadır: birleşik uç dizini sys.path'ten çıkardıktan sonra
        # işlev içindeki bir `import` çağrı anında düşer.
        self.assertFalse(re.findall(r"^[ \t]+(?:import|from)\s+\w+", kaynak, re.M))


if __name__ == "__main__":
    unittest.main()
