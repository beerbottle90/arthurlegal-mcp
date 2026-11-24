"""Kalite bekçileri: mevzuat bekçisi (süreç 7) ve model/platform bekçisi (süreç 8). Ağsız, `gh` çağrılmaz.

    python -m unittest discover -s tests -p test_kalite_bekcileri.py -v      # depo kökünden

Resmî Gazete örneği gerçek fihristtir (24.07.2025, S. 32965; üç kanun "––" olmadan, numarayla başlar). Sürüm notu ve
model sayfaları gerçek sayfaların yapısını taklit eden kısa sentetik metinlerdir (satıcı metni kopyalanmaz).
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KOK, "kalite"))

import mevzuat_bekcisi as mb  # noqa: E402
import model_bekcisi as md  # noqa: E402
import ortak  # noqa: E402

FIX = os.path.join(KOK, "tests", "fixtures", "kalite")
GUN = date(2025, 7, 24)
with open(os.path.join(FIX, "rg_20250724.htm"), "rb") as _fh:
    RG_GUN = _fh.read()

KANUN_7554 = ("<html><body><p>KANUN</p><p>BAZI KANUNLARDA DEĞİŞİKLİK YAPILMASINA DAİR KANUN</p><p>Kanun No. 7554</p>"
              "<p>MADDE 1- 9/8/1983 tarihli ve 2872 sayılı Çevre Kanununun 10 uncu maddesinin ikinci fıkrasında …</p>"
              "<p>MADDE 2- 4/6/1985 tarihli ve 3213 sayılı Maden Kanununun 3 üncü maddesinin …</p>"
              "<p>Tahsil Dairesi: Bu Kanuna göre 6183 sayılı Kanun kapsamında takibi gereken alacaklardan …</p>"
              "<p>… 21/7/1983 tarihli ve 2863 sayılı Kültür ve Tabiat Varlıklarını Koruma Kanunu kapsamında …</p>"
              "</body></html>").encode("cp1254")


def rg_sayfa(gun, kalemler=(), sayi="33500", mukerrerler=()):
    """Resmî Gazete fihristi biçiminde sayfa (hesap-mcp/tests/test_tazelik.py ile aynı düzen)."""
    p = ["<html><head><title>T.C. Resmî Gazete</title></head><body>",
         "<p align=center><b><span> %d Kasım %d Tarihli ve %s Sayılı Resmî\n Gazete</span></b></p>"
         % (gun.day, gun.year, sayi)]
    bolum = None
    for b, baslik, href in kalemler:
        if b != bolum:
            p.append('<p class="MsoNormal"><span class="Normal1"><b><u><font>%s</font></u></b></span></p>' % b)
            bolum = b
        p.append('<p class="MsoNormal"><span class="Normal1"><font size="1"><a href="%s" style="text-decoration: '
                 'none">––&nbsp; %s</a></font></span></p>' % (href, baslik))
    for n in mukerrerler:
        p.append('<p><a href="https://www.resmigazete.gov.tr/eskiler/%04d/%02d/%sM%d.htm">%d. mükerrer</a></p>'
                 % (gun.year, gun.month, gun.strftime("%Y%m%d"), n, n))
    p.append("</body></html>")
    return "\n".join(p).encode("cp1254")


def paket_klasoru(kok, dosyalar):
    """{(paket, göreli yol): metin} → ArthurLegal-<paket>-v1.0.0-Public-Release/knowledge/<yol>."""
    for (paket, yol), metin in dosyalar.items():
        tam = os.path.join(kok, "ArthurLegal-%s-v1.0.0-Public-Release" % paket, "knowledge", *yol.split("/"))
        os.makedirs(os.path.dirname(tam), exist_ok=True)
        with open(tam, "w", encoding="utf-8") as fh:
            fh.write(metin)


REHBERLER = {
    ("Law-Firm", "references/hmk-rehberi.md"): "HMK m. 353 ve HMK'nın 361. maddesi; 6100 sayılı Kanun.",
    ("Courthouse", "references/hmk-rehberi.md"): "HMK m. 353 HMK m. 354 HMK m. 355",
    ("Law-Firm", "references/ced-rehberi.md"): "Çevre Kanunu (2872 m. 25) idari para cezası; 2872 sayılı.",
    ("Law-Firm", "references/vergi.md"): "6183 sayılı Amme Alacaklarının Tahsil Usulü Hakkında Kanun, AATUHK m. 58.",
    ("CorporateAssistant", "references/noter.md"): "Noterlik Kanunu Yönetmeliği'nin 90. maddesi.",
    ("CorporateAssistant", "references/yil.md"): "2004 yılında ve 2004 Yılı Programı; ayrıca AYDIN.",
}


class SahteGetirici:
    """URL sonu → (durum, gövde); tanımsız URL 404. İstenenleri kaydeder."""

    def __init__(self, ozel=None, varsayilan=(404, b"")):
        self.ozel = dict(ozel or {})
        self.varsayilan = varsayilan
        self.istenen = []

    def __call__(self, url, zaman_asimi=10, **kw):
        self.istenen.append(url)
        for son, cevap in self.ozel.items():
            if url.endswith(son):
                if isinstance(cevap, Exception):
                    raise cevap
                return cevap
        return self.varsayilan


def _gh_yok(*_a, **_k):
    raise OSError("gh yok (test)")


# =========================================================================== süreç 7
class Fihrist(unittest.TestCase):
    def test_gercek_fihristte_kanunlar_da_okunur(self):
        f = mb.fihrist(mb.tz._coz(RG_GUN), mb.tz.gun_url(GUN), GUN)
        self.assertEqual(f["sayi"], "32965")
        kanunlar = [k for k in f["kalemler"] if mb.tur_bul(k) == "kanun"]
        self.assertEqual([k["baslik"].split()[0] for k in kanunlar], ["7554", "7555", "7557"])
        self.assertEqual(kanunlar[0]["url"], "https://www.resmigazete.gov.tr/eskiler/2025/07/20250724-1.htm")
        self.assertEqual(kanunlar[0]["bolum"], "KANUNLAR")
        # mcp #75'ten beri ortak ayrıştırıcı (tazelik) da kanunları okur; bekçinin kendi okuması yalnız
        # eksik kalemi ekler, aynı kalemi iki kez eklemez.
        tz_kalemler = mb.tz.fihrist_ayristir(mb.tz._coz(RG_GUN), mb.tz.gun_url(GUN), GUN)["kalemler"]
        self.assertEqual(len(f["kalemler"]), len(tz_kalemler))
        self.assertEqual({k["url"] for k in tz_kalemler if mb.tur_bul(k) == "kanun"}, {k["url"] for k in kanunlar})
        self.assertEqual(len({k["url"] for k in f["kalemler"]}), len(f["kalemler"]))

    def test_turler_ve_gurultu(self):
        f = mb.fihrist(mb.tz._coz(RG_GUN), mb.tz.gun_url(GUN), GUN)
        turler = {}
        for k in f["kalemler"]:
            t = mb.tur_bul(k)
            if t:
                turler.setdefault(t, []).append(k)
        self.assertEqual(sorted(turler), ["cb_karari", "kanun", "yonetmelik"])
        elenen = [k["baslik"] for t, ks in turler.items() for k in ks if mb.gurultu_mu(k, t)]
        self.assertTrue(any("Arazi Toplulaştırması" in b for b in elenen))
        self.assertTrue(any("Özelleştirme" in b for b in elenen))
        self.assertTrue(any("Üniversitesi" in b for b in elenen))
        self.assertFalse(any(b.startswith("4760") for b in elenen))      # ÖTV kararı kalır
        self.assertFalse(any(mb.gurultu_mu(k, "kanun") for k in turler["kanun"]))

    def test_icthadi_birlestirme_basliktan(self):
        k = {"bolum": "YARGITAY KARARI", "baslik": "Yargıtay İçtihadı Birleştirme Büyük Genel Kurulunun "
                                                   "E: 2025/1, K: 2026/1 Sayılı Kararı"}
        self.assertEqual(mb.tur_bul(k), "ibk")
        self.assertEqual(mb.tur_bul({"bolum": "DANIŞTAY KARARI", "baslik": "Danıştay İçtihatları Birleştirme "
                                                                           "Kurulu Kararı"}), "ibk")
        self.assertIsNone(mb.tur_bul({"bolum": "YARGITAY KARARI", "baslik": "Yargıtay 3. Hukuk Dairesine Ait Karar"}))
        self.assertIsNone(mb.tur_bul({"bolum": "ATAMA KARARLARI", "baslik": "Atamalar Hakkında Kararlar"}))
        self.assertIsNone(mb.tur_bul({"bolum": "TEBLİĞLER", "baslik": "Vergi Usul Kanunu Genel Tebliği"}))


class Atiflar(unittest.TestCase):
    def test_kanun_metni(self):
        a = {(x["tur"], x["no"]): x for x in mb.atiflar(mb.ortak.duz(KANUN_7554.decode("cp1254")))}
        self.assertEqual(set(a), {("kanun", "2872"), ("kanun", "3213"), ("kanun", "6183"), ("kanun", "2863")})
        self.assertEqual(a[("kanun", "2872")]["orta"], "Çevre")
        self.assertEqual(a[("kanun", "6183")]["orta"], "")

    def test_karar_kararname_khk(self):
        metin = ("5/1/2021 Tarihli ve 3357 Sayılı Cumhurbaşkanı Kararının; 10/7/2018 tarihli ve 1 sayılı "
                 "Cumhurbaşkanlığı Teşkilatı Hakkında Cumhurbaşkanlığı Kararnamesinin; 635 Sayılı Kanun Hükmünde "
                 "Kararnamede; Türk Parasının Kıymetini Koruma Hakkında 32 Sayılı Karar")
        a = {(x["tur"], x["no"]) for x in mb.atiflar(metin)}
        self.assertEqual(a, {("karar", "3357"), ("cbk", "1"), ("khk", "635"), ("karar", "32")})

    def test_degisen_ad(self):
        self.assertEqual(mb.degisen_ad("Noterlik Kanunu Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik"),
                         "Noterlik Kanunu Yönetmeliği")
        self.assertEqual(mb.degisen_ad("Geçiş Üstünlüğüne Sahip Araçlar Hakkında Yönetmelikte Değişiklik "
                                       "Yapılmasına Dair Yönetmelik"), "Geçiş Üstünlüğüne Sahip Araçlar Hakkında Yönetmelik")
        self.assertEqual(mb.degisen_ad("Destekleme ve Fiyat İstikrar Fonu Hakkında Kararda Değişiklik Yapılmasına "
                                       "Dair Karar (Karar Sayısı: 10116)"), "Destekleme ve Fiyat İstikrar Fonu Hakkında Karar")
        self.assertEqual(mb.degisen_ad("Gaziantep Üniversitesi Strateji Merkezi Yönetmeliğinin Yürürlükten "
                                       "Kaldırılmasına Dair Yönetmelik"), "Gaziantep Üniversitesi Strateji Merkezi Yönetmeliği")
        self.assertEqual(mb.degisen_ad("Diyaliz Merkezleri Hakkında Yönetmelik"), "Diyaliz Merkezleri Hakkında Yönetmelik")


class Kaliplar(unittest.TestCase):
    def test_numara(self):
        k = mb.no_kalibi("6100")
        for olumlu in ("6100 sayılı HMK", "6100 SAYILI", "Kanun No. 6100", "(Kanun No: 6100,", "6100 m. 3", "6100 HMK"):
            self.assertTrue(k.search(olumlu), olumlu)
        for olumsuz in ("16100 sayılı", "6100,5 TL", "61001", "6100 yılında", "6100 Yılı", "1.6100 sayılı"):
            self.assertFalse(k.search(olumsuz), olumsuz)
        self.assertTrue(mb.no_kalibi("4857").search("4857 İş K. m. 18"))

    def test_kisaltma_sinirli(self):
        self.assertTrue(mb._sinirli("HMK").search("HMK'nın 353. maddesi"))
        self.assertFalse(mb._sinirli("HMK").search("HMKY ve AHMK"))
        self.assertTrue(mb._sinirli("İİK").search("(İİK m. 72/7)"))
        self.assertTrue(mb._sinirli("AY", True).search("AY m. 36"))
        self.assertFalse(mb._sinirli("AY", True).search("AYDIN ve AY sonu"))

    def test_tablo_cakisma_yok(self):
        """Bir kısaltma iki kanuna gitmemeli (ör. TMK: Medeni Kanun; Terörle Mücadele Kanunu değil)."""
        tablo = mb.tablo_yukle()
        gorulen = {}
        for no, k in tablo["kanunlar"].items():
            for s in k["kisa"] + k["kisa_m"]:
                self.assertNotIn(s, gorulen, "%s: %s ve %s" % (s, gorulen.get(s), no))
                gorulen[s] = no
        self.assertEqual(gorulen["TMK"], "4721")
        self.assertTrue({"657", "5018"} <= tablo["genel_dayanak"])


class Paketler(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        paket_klasoru(self.d.name, REHBERLER)
        self.p = mb.Paketler(self.d.name)
        self.tablo = mb.tablo_yukle()

    def tearDown(self):
        self.d.cleanup()

    def test_paket_adlari_ve_birlesme(self):
        self.assertEqual(len(self.p.dosyalar), len(REHBERLER))
        r = self.p.ara(mb.kanun_terimleri("6100", "", self.tablo))
        self.assertEqual(set(r), {"references/hmk-rehberi.md"})
        self.assertEqual(sorted(r["references/hmk-rehberi.md"]["paketler"]), ["Courthouse", "Law-Firm"])
        self.assertEqual(r["references/hmk-rehberi.md"]["terimler"]["HMK"], 3)     # en büyük sayı, toplam değil

    def test_yil_ve_sehir_eslesmez(self):
        r = self.p.ara(mb.kanun_terimleri("2004", "", self.tablo) + mb.kanun_terimleri("2709", "", self.tablo))
        self.assertNotIn("references/yil.md", r)

    def test_ad_ile(self):
        r = self.p.ara([("Noterlik Kanunu Yönetmeliği", mb.ad_kalibi("Noterlik Kanunu Yönetmeliği"), True)])
        self.assertEqual(set(r), {"references/noter.md"})


def _calis(getir, paket_kok, gun=1, bugun=GUN, sinir=None):
    istemci = ortak.Nazik(getir, aralik=0, sinir=sinir or mb.ISTEK_SINIRI, uyu=lambda s: None)
    return mb.calis(bugun, gun, mb.Paketler(paket_kok), istemci, mb.tablo_yukle()), istemci


class Akis(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        paket_klasoru(self.d.name, REHBERLER)

    def tearDown(self):
        self.d.cleanup()

    def test_gercek_gun(self):
        g = SahteGetirici({"/20250724.htm": (200, RG_GUN), "20250724-1.htm": (200, KANUN_7554)})
        s, istemci = _calis(g, self.d.name)
        self.assertEqual(s["hatalar"], [])
        sec = {k["baslik"].split()[0]: k for k in s["secilen"]}
        k7554 = sec["7554"]
        self.assertEqual(k7554["metin_durumu"], "okundu")
        self.assertEqual({d["no"] for d in k7554["dokunulan"]}, {"7554", "2872", "3213", "6183", "2863"})
        self.assertEqual(set(k7554["rehberler"]), {"references/ced-rehberi.md", "references/vergi.md"})
        self.assertTrue(k7554["issue"])
        # Metni okunamayan kanun yine issue açar (başlıktaki adla aranır).
        self.assertTrue(sec["7555"]["issue"])
        self.assertIn("HTTP 404", sec["7555"]["metin_durumu"])
        self.assertIn("1567", {d["no"] for d in sec["7555"]["dokunulan"]})
        # Eşleşmeyen yönetmelik ve karar issue açmaz; elenenler metin için istek harcamaz.
        self.assertFalse(sec["Fahri"]["issue"])
        self.assertEqual(len(s["elenen"]), 7)
        self.assertEqual(s["istek"], 1 + len(s["secilen"]))
        self.assertFalse(any(u.endswith(("-7.pdf", "-11.pdf", "-14.htm")) for u in g.istenen))
        a = mb.adaylar(s, self.d.name)
        self.assertEqual({x["tur"] for x in a}, {"degisiklik"})
        for x in a:
            self.assertTrue(x["title"].startswith("Mevzuat değişikliği: "))
            self.assertLessEqual(len(x["title"]), 256)
        govde = [x["body"] for x in a if "7554" in x["title"]][0]
        self.assertIn("### 2872 sayılı Çevre Kanunu: 1 rehber", govde)
        self.assertIn("`references/ced-rehberi.md` | Law-Firm |", govde)
        self.assertIn("RG 24.07.2025, S. 32965", govde)

    def test_ibk_kisaltmayla_ve_yonetmelik_adiyla(self):
        g0 = date(2026, 11, 16)
        sayfa = rg_sayfa(g0, [
            ("YÖNETMELİKLER", "Noterlik Kanunu Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik", "20261116-1.htm"),
            ("YÖNETMELİKLER", "Diyaliz Merkezleri Hakkında Yönetmelik", "20261116-2.htm"),
            ("CUMHURBAŞKANI KARARLARI", "154 kV Enerji İletim Hattı Projesi Kapsamında Bazı Taşınmazların Acele "
                                        "Kamulaştırılması Hakkında Karar (Karar Sayısı: 12000)", "20261116-3.pdf"),
            ("YARGITAY KARARI", "Yargıtay İçtihadı Birleştirme Büyük Genel Kurulunun E: 2025/1, K: 2026/1 Sayılı "
                                "Kararı", "20261116-4.htm")])
        ibk = "<html><body><p>… HMK m. 353 uyarınca istinaf … 2004 sayılı Kanunun 68. maddesi …</p></body></html>"
        diyaliz = "<html><body><p>Dayanak MADDE 2- 7/5/1987 tarihli ve 3359 sayılı Sağlık Hizmetleri Temel Kanunu ile 10/7/2018 tarihli ve 1 sayılı Cumhurbaşkanlığı Teşkilatı Hakkında Cumhurbaşkanlığı Kararnamesine dayanılarak …</p></body></html>"
        g = SahteGetirici({"/20261116.htm": (200, sayfa), "-4.htm": (200, ibk.encode("cp1254")),
                           "-2.htm": (200, diyaliz.encode("cp1254")), "-1.htm": (500, b"")})
        s, _ = _calis(g, self.d.name, bugun=g0)
        sec = {k["tur"] + ":" + k["baslik"][:8]: k for k in s["secilen"]}
        ibk_k = sec["ibk:Yargıtay"]
        self.assertTrue(ibk_k["issue"])
        self.assertTrue({"6100", "2004"} <= {d["no"] for d in ibk_k["dokunulan"]})
        self.assertIn("references/hmk-rehberi.md", ibk_k["rehberler"])
        noter = sec["yonetmelik:Noterlik"]
        self.assertTrue(noter["issue"])                      # metin okunamadı ama ad eşleşti
        self.assertIn("HTTP 500", noter["metin_durumu"])
        self.assertEqual(set(noter["rehberler"]), {"references/noter.md"})
        diy = sec["yonetmelik:Diyaliz "]
        self.assertFalse(diy["issue"])
        self.assertNotIn("1", {d["no"] for d in diy["dokunulan"] if d["tur"] == "cbk"})   # genel dayanak CBK sayılmaz
        self.assertEqual([k["baslik"][:6] for k in s["elenen"]], ["154 kV"])

    def test_erisim_hatasi_uyari_issue(self):
        g = SahteGetirici({"/20250724.htm": (200, RG_GUN)}, varsayilan=(503, b""))
        s, _ = _calis(g, self.d.name, gun=2)
        self.assertEqual(len(s["hatalar"]), 1)
        self.assertIn("23.07.2025", s["hatalar"][0])
        uyari = [x for x in mb.adaylar(s, self.d.name) if x["tur"] == "uyari"]
        self.assertEqual([x["title"] for x in uyari], [mb.UYARI_TARAMA])

    def test_zaman_asimi_sonda_bir_kez_daha_denenir(self):
        sayac = {"n": 0}

        def getir(url, zaman_asimi=10, **kw):
            if url.endswith("/20250724.htm"):
                sayac["n"] += 1
                if sayac["n"] == 1:
                    raise ortak.ErisimHatasi("URLError: timed out")
                return 200, RG_GUN
            return 404, b""
        s, _ = _calis(getir, self.d.name, gun=2)
        self.assertEqual(s["hatalar"], [])
        self.assertEqual(s["gunler"]["2025-07-24"]["durum"], "tamam")
        self.assertEqual(sayac["n"], 2)

    def test_uygun_bulma_kanunu_eslesmezse_issue_yok(self):
        g0 = date(2026, 11, 14)
        sayfa = ("<html><body><p><b>14 Kasım 2026 Tarihli ve 33430 Sayılı Resmî Gazete</b></p><p><b>YASAMA BÖLÜMÜ</b>"
                 "</p><p><b>KANUN</b></p><p><a href=\"20261114-1.htm\">7597 Türkiye Cumhuriyeti ile X Arasında Bir "
                 "Anlaşmanın Onaylanmasının Uygun Bulunduğuna Dair Kanun</a></p><p><a href=\"20261114-2.htm\">7598 "
                 "Hukuk Muhakemeleri Kanunu ile Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun</a></p>"
                 "</body></html>").encode("cp1254")
        s, _ = _calis(SahteGetirici({"/20261114.htm": (200, sayfa)}), self.d.name, bugun=g0)
        sec = {k["baslik"][:4]: k for k in s["secilen"]}
        self.assertFalse(sec["7597"]["issue"])
        self.assertTrue(sec["7598"]["issue"])                 # metin okunamadı, başlıktaki adla HMK bulundu
        self.assertIn("references/hmk-rehberi.md", sec["7598"]["rehberler"])

    def test_yalniz_dayanak_eslesmesi_govdede_yazar(self):
        g0 = date(2026, 11, 13)
        sayfa = rg_sayfa(g0, [("YÖNETMELİKLER", "Diyaliz Merkezleri Hakkında Yönetmelik", "20261113-1.htm")])
        metin = ("<html><body><p>Dayanak MADDE 2- 6183 sayılı Amme Alacaklarının Tahsil Usulü Hakkında Kanuna "
                 "dayanılarak hazırlanmıştır.</p></body></html>").encode("cp1254")
        s, _ = _calis(SahteGetirici({"/20261113.htm": (200, sayfa), "-1.htm": (200, metin)}), self.d.name, bugun=g0)
        self.assertTrue(s["secilen"][0]["issue"])
        self.assertIn("Eşleşme yalnız dayanak kanunundan", mb.issue_govdesi(s["secilen"][0]))

    def test_hepsi_404_hata(self):
        s, _ = _calis(SahteGetirici(), self.d.name, gun=3)
        self.assertTrue(s["hatalar"])
        self.assertIn(mb.UYARI_TARAMA, [x["title"] for x in mb.adaylar(s, self.d.name)])

    def test_mukerrer_okunur(self):
        g0 = date(2026, 11, 17)
        ana = rg_sayfa(g0, [("TEBLİĞLER", "Bir Tebliğ", "20261117-1.htm")], mukerrerler=[1])
        muk = rg_sayfa(g0, [("YÖNETMELİKLER", "Noterlik Kanunu Yönetmeliğinde Değişiklik Yapılmasına Dair "
                                              "Yönetmelik", "20261117M1-1.htm")])
        g = SahteGetirici({"/20261117.htm": (200, ana), "/20261117M1.htm": (200, muk)})
        s, _ = _calis(g, self.d.name, bugun=g0)
        self.assertEqual([(k["tur"], k["mukerrer"]) for k in s["secilen"]], [("yonetmelik", 1)])
        self.assertIn("(1. Mükerrer)", mb.issue_govdesi(s["secilen"][0]))

    def test_istek_siniri(self):
        g = SahteGetirici({"/20250724.htm": (200, RG_GUN), "20250724-1.htm": (200, KANUN_7554)})
        s, istemci = _calis(g, self.d.name, sinir=2)
        self.assertEqual(istemci.sayi, 2)
        durumlar = [k["metin_durumu"] for k in s["secilen"]]
        self.assertEqual(durumlar[0], "okundu")
        self.assertTrue(all("istek sınırı" in d for d in durumlar[1:]))

    def test_sure_dolarsa_kalan_gunler_uyariya(self):
        an = [0.0]

        def getir(url, zaman_asimi=10, **kw):
            an[0] += 100                                   # her istek 100 sn sürüyor (yavaş RG)
            return (200, RG_GUN) if url.endswith("/20250724.htm") else (404, b"")
        istemci = ortak.Nazik(getir, aralik=0, sinir=80, uyu=lambda s: None, sayac=lambda: an[0], sure=150)
        s = mb.calis(GUN, 4, mb.Paketler(self.d.name), istemci, mb.tablo_yukle())
        self.assertTrue(any("süre sınırı" in h for h in s["hatalar"]))
        self.assertIn(mb.UYARI_TARAMA, [x["title"] for x in mb.adaylar(s, self.d.name)])

    def test_paket_yoksa_uyari(self):
        with tempfile.TemporaryDirectory() as bos:
            s, _ = _calis(SahteGetirici({"/20250724.htm": (200, RG_GUN)}), bos)
            self.assertIn(mb.UYARI_PAKET, [x["title"] for x in mb.adaylar(s, bos)])

    def test_govde_siniri(self):
        rehber = {"references/r%03d.md" % i: {"paketler": ["Law-Firm"], "terimler": {"HMK": i + 1}} for i in range(40)}
        k = {"tur": "kanun", "tarih": "2026-11-16", "sayi": "1", "baslik": "7600 Torba Kanun", "url": "u",
             "metin_durumu": "okundu", "rehberler": rehber,
             "dokunulan": [{"tur": "kanun", "no": str(1000 + i), "ad": "Kanun %d " % i + "x" * 80, "nasil": "metinde atıf",
                            "rehber": 40, "rehberler": rehber} for i in range(300)]}
        g = mb.issue_govdesi(k)
        self.assertLessEqual(len(g), mb.GOVDE_AZAMI + 1000)
        self.assertIn("issue sınırı", g)
        self.assertIn("## Aylık bakım saatinde", g)


class Komut(unittest.TestCase):
    def test_kuru_kosu_issue_acmaz(self):
        with tempfile.TemporaryDirectory() as d:
            paket_klasoru(d, REHBERLER)
            eski = (ortak.mevcut_issuelar, ortak.issue_ac, ortak.etiket_kur, ortak.acik_issue)
            acilan = []
            ortak.mevcut_issuelar, ortak.etiket_kur, ortak.acik_issue = _gh_yok, _gh_yok, _gh_yok
            ortak.issue_ac = lambda *a: acilan.append(a)
            ozet = os.environ.pop("GITHUB_STEP_SUMMARY", None)
            try:
                cikti = io.StringIO()
                with redirect_stdout(cikti):
                    kod = mb.main(["--paketler", d, "--bugun", "2025-07-24", "--gun", "1", "--kuru",
                                   "--cikti", os.path.join(d, "o", "s.json")],
                                  getir=SahteGetirici({"/20250724.htm": (200, RG_GUN)}), uyu=lambda s: None)
            finally:
                ortak.mevcut_issuelar, ortak.issue_ac, ortak.etiket_kur, ortak.acik_issue = eski
                if ozet is not None:
                    os.environ["GITHUB_STEP_SUMMARY"] = ozet
            self.assertEqual(kod, 0)
            self.assertEqual(acilan, [])
            self.assertIn("Açılırdı (kuru): Mevzuat değişikliği: Kanun, RG 24.07.2025: 7554", cikti.getvalue())
            with open(os.path.join(d, "o", "s.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["gunler"]["2025-07-24"]["sayi"], "32965")

    def test_gun_girdisi_sinirlanir(self):
        g = SahteGetirici()
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()):
            eski = ortak.mevcut_issuelar, ortak.acik_issue
            ortak.mevcut_issuelar = ortak.acik_issue = _gh_yok
            try:
                mb.main(["--paketler", d, "--bugun", "2025-07-24", "--gun", "99; rm -rf /", "--kuru"], getir=g,
                        uyu=lambda s: None)
                n_kotu = len(g.istenen)
                g.istenen.clear()
                mb.main(["--paketler", d, "--bugun", "2025-07-24", "--gun", "30", "--kuru"], getir=g, uyu=lambda s: None)
            finally:
                ortak.mevcut_issuelar, ortak.acik_issue = eski
        self.assertEqual(n_kotu, mb.GUN_VARSAYILAN)
        self.assertEqual(len(g.istenen), mb.GUN_AZAMI)


class Tasima(unittest.TestCase):
    """Issue #89: hafta aralığı dışına düşen taranamamış gün sonraki koşuda kendiliğinden taranır."""

    # issue #89'un gövdesi (işaret satırı yokken açılmış eski biçim)
    ESKI_GOVDE = ("Haftalık mevzuat bekçisi Resmî Gazete taramasını tamamlayamadı; aşağıdaki günlerdeki yayımlar "
                  "denetlenmedi.\n\n- 22.11.2026: URLError: timed out\n\nYapılacak: …")

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        paket_klasoru(self.d.name, REHBERLER)
        self.eski = (ortak.mevcut_issuelar, ortak.etiket_kur, ortak.issue_ac, ortak.acik_issue,
                     ortak.issue_guncelle, ortak.issue_kapat)
        self.olay = []
        ortak.mevcut_issuelar = lambda e: [{"title": mb.UYARI_TARAMA, "state": "OPEN"}]
        ortak.etiket_kur = lambda *a: None
        ortak.issue_ac = lambda *a: self.olay.append(("ac",) + a)
        ortak.issue_guncelle = lambda no, govde: self.olay.append(("guncelle", no, govde))
        ortak.issue_kapat = lambda no, yorum: self.olay.append(("kapat", no, yorum))
        self.ozet = os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def tearDown(self):
        (ortak.mevcut_issuelar, ortak.etiket_kur, ortak.issue_ac, ortak.acik_issue,
         ortak.issue_guncelle, ortak.issue_kapat) = self.eski
        if self.ozet is not None:
            os.environ["GITHUB_STEP_SUMMARY"] = self.ozet
        self.d.cleanup()

    def _main(self, getir, bugun, govde, kuru=False):
        ortak.acik_issue = lambda etiket, baslik: {"number": 89, "title": baslik, "body": govde}
        arg = ["--paketler", self.d.name, "--bugun", bugun, "--gun", "7"] + (["--kuru"] if kuru else [])
        cikti = io.StringIO()
        with redirect_stdout(cikti):
            kod = mb.main(arg, getir=getir, uyu=lambda s: None)
        return kod, cikti.getvalue()

    def test_isaret_ve_eski_bicim_okunur(self):
        govde = mb.tarama_uyarisi(["24.07.2025: URLError: timed out"], ["2025-07-24"])
        self.assertIn("<!-- taranamayan-gunler: 2025-07-24 -->", govde)
        self.assertEqual(mb.tasinan_gunler(govde), [date(2025, 7, 24)])
        self.assertEqual(mb.tasinan_gunler(self.ESKI_GOVDE), [date(2026, 11, 22)])
        self.assertEqual(mb.tasinan_gunler("işaretsiz ve satırsız"), [])
        self.assertEqual(mb.tasinan_gunler("<!-- taranamayan-gunler: 2025-07-24,bozuk,2025-13-40 -->"),
                         [date(2025, 7, 24)])

    def test_iki_denemede_de_zaman_asimi_isarete_yazilir(self):
        g = SahteGetirici({"/20250724.htm": ortak.ErisimHatasi("URLError: timed out")})
        s, _ = _calis(g, self.d.name, gun=2)
        self.assertEqual(sum(u.endswith("/20250724.htm") for u in g.istenen), 2)   # aynı koşuda yeniden denendi
        uyari = [x for x in mb.adaylar(s, self.d.name) if x["tur"] == "uyari"][0]
        self.assertEqual(mb.tasinan_gunler(uyari["body"]), [date(2025, 7, 24)])

    def test_tasinan_gun_sonraki_kosuda_taranir_ve_issue_kapanir(self):
        # Koşu 1 (24.07): gün iki kez zaman aşımı → uyarı gövdesi günü taşır.
        g1 = SahteGetirici({"/20250724.htm": ortak.ErisimHatasi("URLError: timed out")})
        s1, _ = _calis(g1, self.d.name, gun=1)
        govde = [x for x in mb.adaylar(s1, self.d.name) if x["tur"] == "uyari"][0]["body"]
        # Koşu 2 (bir hafta sonra, 31.07, aralık 25.07 ile 31.07): 24.07 aralık dışında ama yine okunur.
        g2 = SahteGetirici({"/20250724.htm": (200, RG_GUN)})
        kod, cikti = self._main(g2, "2025-07-31", govde)
        self.assertEqual(kod, 0)
        self.assertTrue(any(u.endswith("/20250724.htm") for u in g2.istenen))
        kapat = [o for o in self.olay if o[0] == "kapat"]
        self.assertEqual(len(kapat), 1)
        self.assertEqual(kapat[0][1], 89)
        self.assertIn("24.07.2025", kapat[0][2])
        self.assertIn("Önceki koşudan taşınan: 24.07.2025", cikti)
        # taşınan gündeki kanun değişikliği de normal yoldan issue olur
        self.assertTrue(any(o[0] == "ac" and "RG 24.07.2025: 7554" in o[1] for o in self.olay))

    def test_eski_bicimdeki_issue_89_de_tasinir(self):
        g = SahteGetirici({"/20261122.htm": (200, RG_GUN)})
        kod, cikti = self._main(g, "2026-11-30", self.ESKI_GOVDE)
        self.assertTrue(any(u.endswith("/20261122.htm") for u in g.istenen))
        self.assertEqual([o[1] for o in self.olay if o[0] == "kapat"], [89])

    def test_hala_okunamazsa_issue_guncellenir_kapanmaz(self):
        govde = mb.tarama_uyarisi(["24.07.2025: URLError: timed out"], ["2025-07-24"])
        g = SahteGetirici({"/20250724.htm": ortak.ErisimHatasi("URLError: timed out"),
                           "/20250731.htm": ortak.ErisimHatasi("URLError: timed out")})
        self._main(g, "2025-07-31", govde)
        self.assertEqual([o for o in self.olay if o[0] == "kapat"], [])
        guncel = [o for o in self.olay if o[0] == "guncelle"]
        self.assertEqual(len(guncel), 1)
        self.assertEqual(mb.tasinan_gunler(guncel[0][2]), [date(2025, 7, 24), date(2025, 7, 31)])

    def test_kuru_kosu_issue_ya_dokunmaz(self):
        govde = mb.tarama_uyarisi(["24.07.2025: URLError: timed out"], ["2025-07-24"])
        kod, cikti = self._main(SahteGetirici({"/20250724.htm": (200, RG_GUN)}), "2025-07-31", govde, kuru=True)
        self.assertEqual([o for o in self.olay if o[0] in ("kapat", "guncelle", "ac")], [])
        self.assertIn("#89 kapanırdı (kuru)", cikti)

    def test_tasima_siniri(self):
        govde = "<!-- taranamayan-gunler: %s -->" % ",".join(
            "2025-06-%02d" % g for g in range(1, 31))
        self.assertEqual(len(mb.tasinan_gunler(govde)), mb.TASIMA_AZAMI)
        self.assertEqual(mb.tasinan_gunler(govde)[-1], date(2025, 6, 30))


# =========================================================================== ortak
class Ortak(unittest.TestCase):
    def test_planla(self):
        adaylar = [{"title": "A", "body": "", "tur": "degisiklik"}, {"title": "A", "body": "", "tur": "degisiklik"},
                   {"title": "B", "body": "", "tur": "degisiklik"}, {"title": "U", "body": "", "tur": "uyari"}]
        self.assertEqual([x["title"] for x in ortak.planla(adaylar, [])], ["A", "B", "U"])
        mevcut = [{"title": "A", "state": "CLOSED"}, {"title": "U", "state": "CLOSED"}]
        self.assertEqual([x["title"] for x in ortak.planla(adaylar, mevcut)], ["B", "U"])
        self.assertEqual([x["title"] for x in ortak.planla(adaylar, [{"title": "U", "state": "OPEN"}])], ["A", "B"])

    def test_nazik(self):
        an = [0.0]
        uyunan = []
        n = ortak.Nazik(lambda u, t, **k: (200, b""), aralik=1.5, sinir=3, uyu=lambda s: (uyunan.append(s),
                        an.__setitem__(0, an[0] + s)), sayac=lambda: an[0])
        for _ in range(3):
            n("https://x")
        self.assertEqual(uyunan, [1.5, 1.5])
        with self.assertRaises(ortak.ErisimHatasi):
            n("https://x")

    def test_nazik_sure_siniri(self):
        an = [100.0]
        n = ortak.Nazik(lambda u, t, **k: (200, b""), aralik=0, sinir=50, uyu=lambda s: None,
                        sayac=lambda: an[0], sure=60)
        n("https://x")
        an[0] += 59
        n("https://x")
        self.assertIsNone(n.neden())
        an[0] += 2
        self.assertIn("süre sınırı (60 sn)", n.neden())
        self.assertEqual(n.kalan, 0)
        with self.assertRaises(ortak.ErisimHatasi):
            n("https://x")

    def test_izinli(self):
        self.assertTrue(ortak.izinli("https://platform.claude.com/docs", md.ALANLAR))
        for kotu in ("http://platform.claude.com/", "https://platform.claude.com.evil.io/",
                     "https://u:p@platform.claude.com/", "https://platform.claude.com:8443/"):
            self.assertFalse(ortak.izinli(kotu, md.ALANLAR), kotu)


# =========================================================================== süreç 8
CLAUDE_NOTLARI = """<html><body><nav><h3>Menü</h3></nav><h1>Release notes</h1>
<div><h2 id="a">November 2026</h2></div><div><h3>November 17, 2026</h3></div>
<div><p><b>Release channel for the desktop app</b></p></div><div><p>Body text with <b>bold</b> words.</p></div>
<div><p>&nbsp;</p></div><div><h3>November 12, 2026</h3></div><div><p>An entry with no bold title line.</p></div>
<div><h2>October 2026</h2></div><div><h3>October 7, 2026</h3></div><div><p><strong>Older entry</strong></p></div>
<h2>Related articles</h2><p><b>Not an entry</b></p></body></html>"""

CHATGPT_NOTLARI = """<html><body><h1>ChatGPT release notes</h1>
<div class="c"><h1>November 18, 2026</h1><h2>Live dictation</h2><p>Text.</p></div>
<div class="c"><h1>November 17, 2026</h1><h2>GPT-9 in ChatGPT</h2><p>Text.</p><h2>MCP connectors for Plus</h2></div>
<div class="c"><h1>November 2, 2026</h1><h2>Desktop app for Windows</h2></div></body></html>"""

PLUGIN_GUNLUGU = """<html><body><h2>Search the docs</h2><h3>Get started</h3><h3>Build</h3>
<h1>Plugin UI changelog</h1><h2>November 2026</h2><h3>New widget lifecycle</h3><h4>Fixes &amp; improvements</h4>
<h2>August 2026</h2><h3>Stable OAuth callbacks</h3><h4>Fixes &amp; improvements</h4><h2>Docs agent</h2><h3>Ask</h3>
</body></html>"""

MODELLER_MD = """---
title: Models overview
---
# Models overview

## Latest models

| Feature | Claude Opus 9 | Claude Haiku 9 |
| :--- | :--- | :--- |
| Claude API ID | `claude-opus-9` | `claude-haiku-9` |
| Claude API alias | `claude-opus-9` | `claude-haiku-9` |

## All models

| Model | Context |
| :--- | :--- |
| [Claude Opus 9](https://platform.claude.com/docs/en/models/opus-9/overview) | 1M |
| [Claude Haiku 9](https://platform.claude.com/docs/en/models/haiku-9/overview) | 1M |
| [Claude Mythos 9](https://x) ([limited availability](https://y)) | 1M |
| Claude Opus 8 ([retired, except on Bedrock](https://z)) | 200K |

## Using the Models API
"""


class Notlar(unittest.TestCase):
    def _k(self, kod):
        return [k for k in md.KAYNAKLAR if k["kod"] == kod][0]

    def test_tarih(self):
        self.assertEqual(md.tarih_coz("November 17, 2026"), "2026-11-17")
        self.assertEqual(md.tarih_coz("November 2026"), "2026-11")
        self.assertEqual(md.tarih_coz("Monday, March 2, 2026"), "2026-03-02")
        self.assertIsNone(md.tarih_coz("Fixes & improvements"))
        self.assertIsNone(md.tarih_coz("February 30, 2026"))

    def test_claude(self):
        k = md.notlari_ayristir(CLAUDE_NOTLARI, self._k("claude-surum"))
        self.assertEqual(k, [{"tarih": "2026-11-17", "baslik": "Release channel for the desktop app"},
                             {"tarih": "2026-11-12", "baslik": "An entry with no bold title line."},
                             {"tarih": "2026-10-07", "baslik": "Older entry"}])

    def test_chatgpt(self):
        k = md.notlari_ayristir(CHATGPT_NOTLARI, self._k("chatgpt-surum"))
        self.assertEqual([x["baslik"] for x in k], ["Live dictation", "GPT-9 in ChatGPT", "MCP connectors for Plus",
                                                     "Desktop app for Windows"])
        suz = self._k("chatgpt-surum")["suzgec"]
        self.assertEqual([x["baslik"] for x in k if suz.search(x["baslik"])],
                         ["GPT-9 in ChatGPT", "MCP connectors for Plus", "Desktop app for Windows"])

    def test_plugin_gunlugu(self):
        k = md.notlari_ayristir(PLUGIN_GUNLUGU, self._k("chatgpt-uygulama"))
        self.assertEqual(k, [{"tarih": "2026-11", "baslik": "New widget lifecycle"},
                             {"tarih": "2026-08", "baslik": "Stable OAuth callbacks"}])

    def test_taninmayan_sayfa(self):
        with self.assertRaises(md.OkumaHatasi):
            md.notlari_ayristir("<html><h1>Just a moment...</h1></html>", self._k("chatgpt-surum"))
        with self.assertRaises(md.OkumaHatasi):
            md.notlari_ayristir("<html><h1>Release notes</h1><p>nothing</p></html>", self._k("claude-surum"))

    def test_yeni_notlar(self):
        kayit = [{"tarih": "2026-11-17", "baslik": "A"}, {"tarih": "2026-11-01", "baslik": "B"},
                 {"tarih": "2026-11", "baslik": "C"}, {"tarih": "2026-10", "baslik": "D"}]
        bugun = date(2026, 11, 19)
        self.assertEqual([x["baslik"] for x in md.yeni_notlar(kayit, None, bugun)], ["A", "C"])
        self.assertEqual([x["baslik"] for x in md.yeni_notlar(kayit, ["2026-11-17|A", "2026-11|C"], bugun)], ["B", "D"])


class Modeller(unittest.TestCase):
    def test_ayristir(self):
        m = md.modelleri_ayristir(MODELLER_MD)
        self.assertEqual(m["guncel"], ["claude-opus-9", "claude-haiku-9"])
        self.assertEqual(m["modeller"], {"Claude Opus 9": "aktif", "Claude Haiku 9": "aktif",
                                         "Claude Mythos 9": "sınırlı erişim", "Claude Opus 8": "emekli"})
        with self.assertRaises(md.OkumaHatasi):
            md.modelleri_ayristir("# Models\n\nNo table here.")

    def test_kimlikten_ad(self):
        self.assertEqual(md.kimlikten_ad("claude-opus-5-6"), "Claude Opus 5.6")
        self.assertEqual(md.kimlikten_ad("claude-opus-4-5-20251101"), "Claude Opus 4.5")
        self.assertEqual(md.kimlikten_ad("claude-fable-5"), "Claude Fable 5")

    def test_olaylar(self):
        once = {"guncel": ["claude-opus-5-5", "claude-haiku-5-5"],
                "modeller": {"Claude Opus 5.5": "aktif", "Claude Haiku 5.5": "aktif", "Claude Opus 4.5": "aktif",
                             "Claude Sonnet 4": "aktif"}}
        simdi = {"guncel": ["claude-opus-5-6", "claude-haiku-5-5"],
                 "modeller": {"Claude Opus 5.6": "aktif", "Claude Opus 5.5": "aktif", "Claude Haiku 5.5": "aktif",
                              "Claude Opus 4.5": "emekli"}}
        ozet = [o["ozet"] for o in md.model_olaylari(once, simdi)]
        self.assertEqual(ozet, ["yeni model Claude Opus 5.6", "Claude Opus 4.5 durumu emekli",
                                "Claude Sonnet 4 listeden çıktı", "claude-opus-5-5 güncel modellerden çıktı"])
        self.assertEqual(md.model_olaylari(simdi, simdi), [])

    def test_taban_dosyasi(self):
        t = md.taban_yukle()
        self.assertTrue(t["guncel"])
        self.assertTrue(t["modeller"])
        self.assertTrue(set(t["modeller"].values()) <= {"aktif", "sınırlı erişim", "emekli", "kullanımdan kalkıyor"})
        for kimlik in t["guncel"]:
            self.assertIn(md.kimlikten_ad(kimlik), t["modeller"])


def _model_getir(ozel=None):
    tum = {"overview.md": (200, MODELLER_MD.encode()), "12138966-release-notes": (200, CLAUDE_NOTLARI.encode()),
           "6825453-chatgpt-release-notes": (200, CHATGPT_NOTLARI.encode()),
           "plugins/changelog": (200, PLUGIN_GUNLUGU.encode())}
    tum.update(ozel or {})
    return SahteGetirici(tum)


class ModelAkis(unittest.TestCase):
    BUGUN = date(2026, 11, 19)

    def test_ilk_kosu_tabanla(self):
        taban = {"guncel": ["claude-opus-9"], "modeller": {"Claude Opus 9": "aktif", "Claude Opus 8": "aktif"}}
        s = md.calis({}, _model_getir(), self.BUGUN, taban)
        basliklar = [a["title"] for a in s["adaylar"]]
        self.assertIn("Model/platform değişikliği: Claude model listesi, yeni model Claude Haiku 9", basliklar)
        self.assertIn("Model/platform değişikliği: Claude model listesi, Claude Opus 8 durumu emekli", basliklar)
        self.assertIn("Model/platform değişikliği: Claude uygulamaları sürüm notları 17.11.2026, Release channel for "
                      "the desktop app", basliklar)
        self.assertIn("Model/platform değişikliği: ChatGPT sürüm notları 17.11.2026, GPT-9 in ChatGPT", basliklar)
        self.assertFalse(any("Live dictation" in b for b in basliklar))          # ilgisiz
        self.assertFalse(any("Desktop app for Windows" in b for b in basliklar))  # 8 günden eski
        self.assertIn("Model/platform değişikliği: ChatGPT uygulama (MCP) değişiklik günlüğü 11.2026, New widget "
                      "lifecycle", basliklar)
        self.assertTrue(all(a["tur"] == "degisiklik" for a in s["adaylar"]))
        govde = [a["body"] for a in s["adaylar"] if "Haiku 9" in a["title"]][0]
        self.assertIn(md.TAVSIYE, govde)
        self.assertEqual(md.TAVSIYE, "Minimum Opus 5.5 · Medium / High effort tavsiye ederiz, bağlayıcı değildir.")

    def test_ikinci_kosu_degisiklik_yoksa_issue_yok(self):
        ilk = md.calis({}, _model_getir(), self.BUGUN, md.modelleri_ayristir(MODELLER_MD))
        ikinci = md.calis(json.loads(json.dumps(ilk["durum"])), _model_getir(), self.BUGUN, {})
        self.assertEqual(ikinci["adaylar"], [])

    def test_okunamazsa_uyari_ve_kayit_korunur(self):
        ilk = md.calis({}, _model_getir(), self.BUGUN, md.modelleri_ayristir(MODELLER_MD))
        s = md.calis(ilk["durum"], _model_getir({"6825453-chatgpt-release-notes": (403, b""),
                                                 "overview.md": (200, b"<html>Just a moment...</html>")}),
                     self.BUGUN, {})
        uyari = [a for a in s["adaylar"] if a["tur"] == "uyari"]
        self.assertEqual([a["title"] for a in uyari], ["Model/platform bekçisi uyarısı: Claude model listesi okunamadı",
                                                       "Model/platform bekçisi uyarısı: ChatGPT sürüm notları okunamadı"])
        self.assertIn("Cloudflare", uyari[1]["body"])
        self.assertEqual(s["durum"]["chatgpt-surum"], ilk["durum"]["chatgpt-surum"])
        self.assertEqual(s["durum"]["claude-modeller"], ilk["durum"]["claude-modeller"])
        self.assertEqual(len(s["uyarilar"]), 2)

    def test_cok_yeni_kayit_tek_uyari(self):
        govde = "<h1>Release notes</h1>" + "".join(
            "<h3>November %d, 2026</h3><p><b>Entry %d</b></p>" % (d, d) for d in range(1, 19))
        s = md.calis({"claude-surum": {"kayitlar": ["2026-10-01|x"]}},
                     _model_getir({"12138966-release-notes": (200, govde.encode())}),
                     self.BUGUN, md.modelleri_ayristir(MODELLER_MD))
        claude = [a for a in s["adaylar"] if "Claude uygulamaları" in a["title"]]
        self.assertEqual([a["tur"] for a in claude], ["uyari"])

    def test_kuru_kosu_kaydi_yazmaz(self):
        with tempfile.TemporaryDirectory() as d:
            yol = os.path.join(d, "durum.json")
            eski = (ortak.mevcut_issuelar, ortak.issue_ac)
            ortak.mevcut_issuelar = _gh_yok
            ortak.issue_ac = lambda *a: self.fail("kuru koşuda issue açıldı")
            try:
                with redirect_stdout(io.StringIO()) as cikti:
                    kod = md.main(["--durum", yol, "--kuru"], getir=_model_getir(), bugun=self.BUGUN, uyu=lambda s: None)
            finally:
                ortak.mevcut_issuelar, ortak.issue_ac = eski
            self.assertEqual(kod, 0)
            self.assertFalse(os.path.exists(yol))
            self.assertIn("Açılırdı (kuru): Model/platform değişikliği:", cikti.getvalue())

    def test_gercek_kosu_kaydi_yazar_ve_issue_acar(self):
        with tempfile.TemporaryDirectory() as d:
            yol = os.path.join(d, ".bekci", "durum.json")
            eski = (ortak.mevcut_issuelar, ortak.issue_ac, ortak.etiket_kur)
            acilan = []
            ortak.mevcut_issuelar = lambda e: [{"title": "Model/platform değişikliği: ChatGPT sürüm notları 17.11.2026, "
                                                         "GPT-9 in ChatGPT", "state": "CLOSED"}]
            ortak.issue_ac = lambda t, b, e: acilan.append((t, e))
            ortak.etiket_kur = lambda *a: None
            try:
                with redirect_stdout(io.StringIO()):
                    kod = md.main(["--durum", yol], getir=_model_getir(), bugun=self.BUGUN, uyu=lambda s: None)
            finally:
                ortak.mevcut_issuelar, ortak.issue_ac, ortak.etiket_kur = eski
            self.assertEqual(kod, 0)
            self.assertTrue(os.path.exists(yol))
            self.assertTrue(acilan)
            self.assertFalse(any("GPT-9" in t for t, _e in acilan))       # kapalı aynı başlık: yeniden açılmaz
            self.assertTrue(all(e == md.ETIKET for _t, e in acilan))


# =========================================================================== iş akışları
def _akis(ad):
    with open(os.path.join(KOK, ".github", "workflows", ad), encoding="utf-8") as fh:
        return fh.read()


# OSS Scanner imajında .github yoktur (.dockerignore): iş akışı dosyaları orada okunamaz, testler.yml'de depoda denetlenir.
# 18.11'den beri bu sınıf imajda düşüyordu; imaj iş akışı ana dalda o günden beri hiç koşmamıştı (24.11.2026).
@unittest.skipUnless(os.path.isdir(os.path.join(KOK, ".github", "workflows")),
                     "imajda .github yok (.dockerignore); iş akışları depoda denetlenir")
class IsAkislari(unittest.TestCase):
    ADLAR = ("mevzuat-bekcisi.yml", "model-bekcisi.yml")

    def test_eylemler_commite_sabit(self):
        for ad in self.ADLAR:
            for satir in re.findall(r"uses:\s*(\S+)", _akis(ad)):
                self.assertRegex(satir, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$", "%s: %s" % (ad, satir))

    def test_yetki_ve_sir(self):
        for ad in self.ADLAR:
            a = _akis(ad)
            self.assertIn("permissions:\n  contents: read\n  issues: write\n", a, ad)
            self.assertNotIn("secrets.", a, ad)
            self.assertIn("GH_TOKEN: ${{ github.token }}", a, ad)

    def test_dallarda_kuru_haftalik(self):
        for ad in self.ADLAR:
            a = _akis(ad)
            self.assertIn("KURU: ${{ github.ref != 'refs/heads/master' && '--kuru' || '' }}", a, ad)
            self.assertIn("branches-ignore: [master]", a, ad)
            self.assertRegex(a, r'cron: "\d+ \d+ \* \* 1"', ad)        # her Pazartesi

    def test_mevzuat_paketleri_salt_okunur(self):
        a = _akis("mevzuat-bekcisi.yml")
        self.assertIn("repository: beerbottle90/ArthurLegal", a)
        self.assertIn("persist-credentials: false", a)
        self.assertIn("/ArthurLegal-*-Public-Release/knowledge/", a)
        self.assertIn('--gun "$GUN"', a)                                   # girdi ortam değişkeniyle, satıra gömülmez
        self.assertNotIn("${{ github.event.inputs.gun }}\n", a.split("run:")[-1])

    def test_model_kaydi_yalniz_masterda(self):
        a = _akis("model-bekcisi.yml")
        kaydet = a.split("actions/cache/save@")[0].rsplit("- name:", 1)[1]
        self.assertIn("if: github.ref == 'refs/heads/master'", kaydet)
        self.assertIn("restore-keys: model-bekcisi-", a)


if __name__ == "__main__":
    unittest.main()
