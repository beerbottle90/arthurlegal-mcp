"""Canlı TKGM katmanı: sıra, devre kesici, önbellek, tarama koruması, ad eşleştirme, onay kartı.

Hiçbir test TKGM'ye ya da OpenStreetMap'e bağlanmaz. Uçtan uca testler 127.0.0.1'de kurulan
sahte bir sunucuya gider (TKGM_API_URL / TKGM_WEB_URL / TKGM_NOMINATIM_URL). Listeler ve
parseller kurgusaldır: il/ilçe adları gerçek, kimlikler, mahalleler ve geometri uydurmadır.
"""

import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
import tkgm_canli as canli  # noqa: E402
from mcpcore import McpError  # noqa: E402

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ILLER = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"id": 34, "text": "İSTANBUL"}, "geometry": None},
    {"type": "Feature", "properties": {"id": 6, "text": "ANKARA"}, "geometry": None},
    {"type": "Feature", "properties": {"id": 35, "text": "İZMİR"}, "geometry": None}]}
ILCELER = [{"id": 1001, "text": "KADIKÖY"}, {"id": 1002, "text": "ÜSKÜDAR"}, {"id": 1003, "text": "BEYOĞLU"}]
MAHALLELER = {"type": "FeatureCollection", "features": [
    {"properties": {"id": 5001, "text": "DENEME"}}, {"properties": {"id": 5002, "text": "YENİ"}},
    {"properties": {"id": 5003, "text": "YENİDOĞAN"}}, {"properties": {"id": 5004, "text": "ÖRNEK KÖYÜ"}}]}


def kare(parsel_no, ada=101):
    x = 29.0300 + (parsel_no % 50) * 0.0006
    y, d = 40.9900, 0.0004
    halka = [[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [halka]},
            "properties": {"ilAd": "İstanbul", "ilceAd": "Kadıköy", "mahalleAd": "Deneme", "adaNo": str(ada),
                           "parselNo": str(parsel_no), "alan": "1.492,00", "nitelik": "Arsa", "mevkii": "",
                           "pafta": "F22-C-11-A", "zeminKmdurum": "Ana Taşınmaz", "durum": "Aktif"}}


class _Sahte(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: A003
        pass

    def _yaz(self, kod, govde, basliklar=None):
        self.send_response(kod)
        for k, v in (basliklar or {"Content-Type": "application/json"}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(govde)))
        self.end_headers()
        self.wfile.write(govde)

    def do_GET(self):  # noqa: N802
        srv = self.server
        with srv.kilit:
            srv.istekler.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        if srv.gecikme:
            time.sleep(srv.gecikme)
        mod = srv.mod
        if mod == "429":
            return self._yaz(429, b"{}", {"Content-Type": "application/json", "Retry-After": "120"})
        if mod == "403":
            return self._yaz(403, b"{}")
        if mod == "html":
            return self._yaz(200, "<html>bakım</html>".encode(), {"Content-Type": "text/html"})
        if mod == "redirect":
            return self._yaz(302, b"", {"Location": "/baska-yer"})
        yol = urllib.parse.urlsplit(self.path).path
        j = lambda v: self._yaz(200, json.dumps(v, ensure_ascii=False).encode("utf-8"))  # noqa: E731
        if yol == "/web/app/modules/administrativeQuery/data/ilListe.json":
            return j(ILLER)
        if yol == "/api/idariYapi/ilceListe/34":
            return j(ILCELER)
        if yol == "/api/idariYapi/mahalleListe/1001":
            return j(MAHALLELER)
        m = re.fullmatch(r"/api/parsel/500[1-4]/(\d+)/(\d+)", yol)
        if m:
            if m.group(2) == "999":
                return self._yaz(404, b'{"Message":"yok"}')
            return j(kare(int(m.group(2)), int(m.group(1))))
        m = re.fullmatch(r"/api/parsel/([\d.]+)/([\d.]+)/", yol)
        if m:
            e, b = float(m.group(1)), float(m.group(2))
            x = 29.0300 + 7 * 0.0006
            if 40.9900 <= e <= 40.9904 and x <= b <= x + 0.0004:
                return j(kare(7))
            return self._yaz(404, b"{}")
        if yol == "/nominatim/search":
            return j([{"lat": "40.9902", "lon": "29.0344", "display_name": "Deneme Parkı, Kadıköy, İstanbul",
                       "category": "leisure", "type": "park",
                       "address": {"province": "İstanbul", "town": "Kadıköy", "suburb": "Deneme"}}])
        return self._yaz(404, b"{}")


_ORTAM = ("MCP_TRANSPORT", "TKGM_DOSYA_ERISIMI", "TKGM_CIKTI", "TKGM_API_URL", "TKGM_WEB_URL",
          "TKGM_NOMINATIM_URL", "TKGM_CANLI", "TKGM_ONAY_KARTI")


class Sahte(unittest.TestCase):
    """Sahte TKGM sunucusu + hızlı kapı + temiz önbellek."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Sahte)
        cls.srv.kilit = threading.Lock()
        cls.srv.istekler, cls.srv.mod, cls.srv.gecikme = [], "", 0.0
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.kok = "http://127.0.0.1:%d/" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in _ORTAM}
        self._argv = sys.argv[:]
        for k in _ORTAM:
            os.environ.pop(k, None)
        sys.argv[:] = ["server.py"]
        os.environ.update(TKGM_API_URL=self.kok + "api/", TKGM_WEB_URL=self.kok + "web/",
                          TKGM_NOMINATIM_URL=self.kok + "nominatim/")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["TKGM_CIKTI"] = self._tmp.name
        self._kapilar = (canli.TKGM, canli.OSM, canli.TARAMA)
        canli.TKGM = canli.Kapi("TKGM", 0.001, 5.0, 10000)
        canli.OSM = canli.Kapi("OpenStreetMap Nominatim", 0.001, 5.0, 10000)
        canli.TARAMA = canli.TaramaKorumasi()
        canli.ONBELLEK.bosalt()
        self.srv.istekler.clear()
        self.srv.mod, self.srv.gecikme = "", 0.0

    def tearDown(self):
        canli.TKGM, canli.OSM, canli.TARAMA = self._kapilar
        canli.ONBELLEK.bosalt()
        self._tmp.cleanup()
        sys.argv[:] = self._argv
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def istek_sayisi(self):
        return len(self.srv.istekler)

    @staticmethod
    def cagir(isleyici, **args):
        return json.loads(isleyici(args))


# --------------------------------------------------------------------------- #
class SiraTesti(unittest.TestCase):
    def test_baslangiclar_arasi_aralik(self):
        s = canli.Sinirlayici(0.05, 5.0)
        anlar = []
        for _ in range(5):
            s.gir()
            anlar.append(time.monotonic())
            s.cik(0.001)
        farklar = [b - a for a, b in zip(anlar, anlar[1:])]
        self.assertTrue(all(f >= 0.045 for f in farklar), farklar)

    def test_ayni_anda_tek_istek(self):
        s = canli.Sinirlayici(0.001, 10.0)
        kilit, durum = threading.Lock(), {"su_an": 0, "en_cok": 0}

        def is_():
            s.gir()
            with kilit:
                durum["su_an"] += 1
                durum["en_cok"] = max(durum["en_cok"], durum["su_an"])
            time.sleep(0.02)
            with kilit:
                durum["su_an"] -= 1
            s.cik(0.02)

        isler = [threading.Thread(target=is_) for _ in range(8)]
        for t in isler:
            t.start()
        for t in isler:
            t.join(10)
        self.assertEqual(durum["en_cok"], 1)

    def test_sira_dolunca_aninda_yogun(self):
        s = canli.Sinirlayici(1.0, 0.5)
        s.gir()
        s.cik(0.001)
        t0 = time.monotonic()
        with self.assertRaises(canli.Yogun):
            s.gir()
        self.assertLess(time.monotonic() - t0, 0.2, "sıra dolu olduğu hâlde beklendi")

    def test_yavas_yanit_araligi_acar_hizli_yanit_indirir(self):
        s = canli.Sinirlayici(0.01, 5.0, azami_aralik=0.08, yavas_sn=0.05)
        for beklenen in (0.02, 0.04, 0.08, 0.08):
            s.gir()
            s.cik(0.2)
            self.assertAlmostEqual(s.aralik, beklenen)
        for _ in range(20):
            s.gir()
            s.cik(0.001)
        self.assertAlmostEqual(s.aralik, 0.04)

    def test_makine_sayisi_toplami_otuzda_tutar(self):
        """Paylaşılan uç iki makinede: süreç başına sınır yarıya iner, toplam dakikada 30 kalır."""
        import subprocess
        betik = ("import tkgm_canli as c; print(c.MAKINE, c.ARALIK_SN, c.GUNLUK_AZAMI, c.OSM.sinir.taban, "
                 "c.dakikada_toplam()); print(c.sinir_metni())")
        for ortam, beklenen in (({"TKGM_MAKINE_SAYISI": "2"}, "2 4.0 1500 2.2 30"),
                                ({"FLY_APP_NAME": "arthurlegal-mcp"}, "2 4.0 1500 2.2 30"),
                                ({"FLY_APP_NAME": "x", "TKGM_MAKINE_SAYISI": "3"}, "3 6.0 1000"),
                                ({}, "1 2.0 3000 1.1 30")):
            env = {k: v for k, v in os.environ.items() if k not in ("TKGM_MAKINE_SAYISI", "FLY_APP_NAME")}
            env.update(ortam, PYTHONIOENCODING="utf-8")
            cikti = subprocess.run([sys.executable, "-c", betik], cwd=KOK, env=env, capture_output=True,
                                   text=True, encoding="utf-8", timeout=60).stdout
            self.assertTrue(cikti.startswith(beklenen), (ortam, cikti))
            self.assertIn("dakikada en çok 30 TKGM isteği", cikti)
            self.assertIn("günde en çok 3000 istek", cikti)

    def test_varsayilan_dakikada_otuz(self):
        self.assertEqual(canli.ARALIK_SN, 2.0)
        self.assertEqual(canli.Sinirlayici(canli.ARALIK_SN, canli.KUYRUK_SN).dakikada(), 30)
        self.assertIn("dakikada en çok 30 TKGM isteği", canli.sinir_metni())
        self.assertEqual(int(canli.KUYRUK_SN / canli.ARALIK_SN), 10)     # sıra kapasitesi


class DevreTesti(unittest.TestCase):
    def setUp(self):
        self.an = [1000.0]
        self.d = canli.Devre(saat=lambda: self.an[0])

    def test_429_katlanarak_bekletir_retry_after_uyulur(self):
        self.d.yavasla(None, "429")
        self.assertAlmostEqual(self.d.kalan(), 60)
        with self.assertRaises(canli.Kapali):
            self.d.denetle("TKGM")
        self.an[0] += 61
        self.d.denetle("TKGM")
        self.d.yavasla(None, "429")
        self.assertAlmostEqual(self.d.kalan(), 120)
        self.an[0] += 121
        self.d.yavasla(300, "429")
        self.assertGreaterEqual(self.d.kalan(), 300)

    def test_uc_hata_sonra_durur(self):
        self.d.hata("x")
        self.d.hata("x")
        self.d.denetle("TKGM")
        self.d.hata("x")
        self.assertAlmostEqual(self.d.kalan(), 60)

    def test_ret_24_saat(self):
        self.d.reddedildi("403")
        self.assertAlmostEqual(self.d.kalan(), 24 * 3600)
        self.assertEqual(self.d.durum()["durum"], "duraklatıldı")


class OnbellekTesti(unittest.TestCase):
    def test_omur_ve_lru(self):
        an = [0.0]
        o = canli.Onbellek(sinir=2, saat=lambda: an[0])
        o.koy("a", 1, 10)
        self.assertEqual(o.al("a"), (1, 0.0))
        an[0] = 11
        self.assertIsNone(o.al("a"))
        o.koy("a", 1, 100)
        o.koy("b", 2, 100)
        o.koy("c", 3, 100)
        self.assertIsNone(o.al("a"))
        self.assertEqual(o.al("c")[0], 3)

    def test_yok_kaydi_none_deger(self):
        o = canli.Onbellek()
        o.koy("x", None, 10)
        self.assertEqual(o.al("x")[0], None)


class TaramaTesti(unittest.TestCase):
    def setUp(self):
        self.an = [0.0]
        self.t = canli.TaramaKorumasi(saat=lambda: self.an[0])

    def test_ardisik_alti_numara_adayi_kapatir(self):
        for n in (10, 11, 12, 13, 14):
            self.t.parsel(1, 5, n)
        with self.assertRaises(canli.Taranamaz):
            self.t.parsel(1, 5, 15)
        with self.assertRaises(canli.Taranamaz):     # ada artık cezalı
            self.t.parsel(1, 5, 40)
        self.t.parsel(1, 6, 15)                       # başka ada serbest
        self.an[0] += 3601
        self.t.parsel(1, 5, 40)

    def test_komsu_sorgusu_tarama_sayilmaz(self):
        for n in (44, 45, 46, 47, 48, 45, 44):        # beş ardışık + tekrar
            self.t.parsel(2, 7, n)

    def test_ada_basina_on_iki_ayri_parsel(self):
        for n in range(2, 26, 2):                     # 12 ayrı, ardışık değil
            self.t.parsel(3, 1, n)
        self.t.parsel(3, 1, 2)                        # tekrar sayılmaz
        with self.assertRaises(canli.Taranamaz):
            self.t.parsel(3, 1, 100)
        self.an[0] += 601
        self.t.parsel(3, 1, 100)

    def test_mahalle_saatte_kirk(self):
        for i in range(40):
            self.an[0] += 1
            self.t.parsel(4, 100 + i // 10, (i % 10) * 3 + 1)
        with self.assertRaises(canli.Taranamaz):
            self.t.parsel(4, 999, 1)

    def test_hucre_izgarasi(self):
        for i in range(20):
            self.t.konum(40.9901 + i * 0.0003, 29.0301)
        self.t.konum(40.9901, 29.0301)                # aynı nokta sayılmaz
        with self.assertRaises(canli.Taranamaz):
            self.t.konum(40.9999, 29.0301)
        self.t.konum(41.0501, 29.0301)                # başka hücre

    def test_gunluk_tavan(self):
        g = canli.GunlukSayac(2)
        g.say("TKGM")
        g.say("TKGM")
        with self.assertRaises(canli.Kapali):
            g.denetle("TKGM")


class AdTesti(unittest.TestCase):
    iller = canli._liste(ILLER)
    mahalleler = canli._liste(MAHALLELER)

    def test_liste_bicimleri_ve_baslik(self):
        self.assertEqual(self.iller[0], {"id": 34, "ad": "İstanbul"})
        self.assertEqual(canli._liste(ILCELER)[0]["ad"], "Kadıköy")
        self.assertEqual(canli.baslik("ÇANKAYA"), "Çankaya")
        self.assertEqual(canli.baslik("Moda"), "Moda")

    def test_turkce_katlama(self):
        for yazim in ("istanbul", "ISTANBUL", "İSTANBUL", "Istanbul", "İstanbul'da", "istanbulda"):
            self.assertEqual(canli.eslestir(yazim.split("'")[0], self.iller, "il")["id"], 34, yazim)

    def test_mahalle_ekleri_ve_bulaniklik(self):
        e = lambda ad: canli.eslestir(ad, self.mahalleler, "mahalle")["id"]  # noqa: E731
        self.assertEqual(e("Yeni"), 5002)
        self.assertEqual(e("Yeni Mahallesi"), 5002)
        self.assertEqual(e("yenidogan"), 5003)
        self.assertEqual(e("Örnek Köyü"), 5004)
        self.assertEqual(e("Denme"), 5001)            # yazım hatası
        self.assertEqual(e("5004"), 5004)             # kimlik

    def test_belirsiz_ve_yok(self):
        with self.assertRaises(canli.Belirsiz) as b:
            canli.eslestir("yen", self.mahalleler, "mahalle")
        self.assertEqual({a["id"] for a in b.exception.adaylar}, {5002, 5003})
        self.assertIn("adaylar", b.exception.yanit())
        with self.assertRaises(canli.Bulunamadi):
            canli.eslestir("Zzzz", self.mahalleler, "mahalle")


class MetinTesti(unittest.TestCase):
    """Listeler önbelleğe elle konur; ağa çıkma girişimi TKGM_CANLI=0 ile Kapali'ya düşer."""

    def setUp(self):
        self._canli = os.environ.get("TKGM_CANLI")
        os.environ["TKGM_CANLI"] = "0"
        canli.ONBELLEK.bosalt()
        for anahtar, veri in ((("il",), ILLER), (("ilce", 34), ILCELER), (("mahalle", 1001), MAHALLELER)):
            canli.ONBELLEK.koy(anahtar, canli._liste(veri), 1e9)

    def tearDown(self):
        canli.ONBELLEK.bosalt()
        if self._canli is None:
            os.environ.pop("TKGM_CANLI", None)
        else:
            os.environ["TKGM_CANLI"] = self._canli

    def test_tam_metin(self):
        c = canli.metin_coz("İstanbul Kadıköy Deneme 101 ada 7 parsel")
        self.assertEqual((c["il"]["id"], c["ilce"]["id"], c["mahalle"]["id"], c["ada"], c["parsel"]),
                         (34, 1001, 5001, 101, 7))

    def test_il_yazilmadan_ekli_ilce(self):
        c = canli.metin_coz("Kadıköy'deki Deneme mahallesi 101/7 parselini getir")
        self.assertEqual((c["il"]["id"], c["mahalle"]["id"], c["ada"], c["parsel"]), (34, 5001, 101, 7))

    def test_koy_parseli_ada_sifir(self):
        c = canli.metin_coz("istanbul kadıköy örnek köyü 0 ada 15 parsel")
        self.assertEqual((c["mahalle"]["id"], c["ada"], c["parsel"]), (5004, 0, 15))

    def test_belirsiz_onek_sessizce_secilmez(self):
        with self.assertRaises(canli.Belirsiz) as b:
            canli.metin_coz("Kadıköy Yen 101 ada 8 parsel")
        self.assertEqual({a["id"] for a in b.exception.adaylar}, {5002, 5003})
        self.assertEqual(b.exception.yanit()["ada"], 101)      # seçimden sonra yinelemek için
        self.assertEqual(b.exception.yanit()["parsel"], 8)
        self.assertEqual(canli.metin_coz("Kadıköy Denem 1 ada 2 parsel")["mahalle"]["id"], 5001)   # tek önek

    def test_ayri_ada_parsel(self):
        c = canli.metin_coz("Kadıköy Yenidoğan", ada=12, parsel=3)
        self.assertEqual((c["mahalle"]["id"], c["ada"], c["parsel"]), (5003, 12, 3))

    def test_koordinat_ve_ters_sira(self):
        self.assertEqual(canli.metin_coz("40.99012, 29.03021"), {"enlem": 40.99012, "boylam": 29.03021})
        self.assertEqual(canli.metin_coz("29,03021; 40,99012"), {"enlem": 40.99012, "boylam": 29.03021})

    def test_eksik_bilgi(self):
        with self.assertRaises(canli.BilgiEksik):
            canli.metin_coz("bir şey sor")
        with self.assertRaises(canli.BilgiEksik):
            canli.metin_coz("Çankaya Kızılay 5 ada 3 parsel")   # il yok, ilçe önbellekte yok: ağa çıkmadan sorar
        with self.assertRaises(canli.Kapali):
            canli.metin_coz("Ankara Çankaya 5 ada 3 parsel")    # Ankara'nın ilçe listesi ağ ister


class AdresGuvenligi(unittest.TestCase):
    def test_liste_uclari_yok_sablonlar_sayisal(self):
        yollar = " ".join(v[1] for v in canli.UCLAR.values())
        self.assertNotIn("adaListe", yollar)
        self.assertNotIn("parselListe", yollar)
        for kotu in ("../x", "1;2", "1/../../x", "1 2", True, "1e5"):
            with self.assertRaises(ValueError):
                canli._adres("mahalle", kotu)
        self.assertTrue(canli._adres("parsel", 1, 0, 5).endswith("parsel/1/0/5"))

    def test_ag_yalniz_canli_modulunde(self):
        with open(os.path.join(KOK, "server.py"), encoding="utf-8") as fh:
            kaynak = fh.read()
        ithalat = {i.split(".")[0] for i in re.findall(r"^\s*(?:import|from)\s+([\w.]+)", kaynak, re.M)}
        self.assertFalse({"socket", "ssl", "http", "urllib", "requests"} & ithalat, ithalat)

    def test_kimlik_ve_surum(self):
        self.assertEqual(canli.SURUM, server.__version__)
        self.assertIn("ArthurLegal-Tapu/%s" % server.__version__, canli.kimlik())
        self.assertIn(canli.MANIFESTO, canli.kimlik())
        canli.kimlik().encode("latin-1")               # HTTP başlığına sığar
        self.assertTrue(canli.MANIFESTO.endswith("tkgm-mcp/docs/MANIFESTO.md"))


class AracAdlari(unittest.TestCase):
    """Birleşik uçta (arthurlegal-mcp) tkgm_ araçları tr_ araçlarıyla yan yana listelenir. Ortak sözcük
    taşıyan ad (tkgm_rehber / tr_hukuk_arastirma_rehberi, tkgm_tarife_ara / tr_mevzuat_ara) modeli yanlış
    araca yöneltir. Liste, arthurlegal-mcp master'daki arthur-tr-hukuk-mcp (eski ArthurLegalTR) v0.5.0 araç adlarıdır."""

    TR = ("aym_ara aym_getir belge_getir hukuk_arastirma_rehberi ictihat_ara ictihat_getir ictihat_semantik_ara "
          "kurum_karari_ara kurum_karari_getir kurum_listesi mevzuat_ara mevzuat_gerekce mevzuat_getir "
          "mevzuat_icinde_ara mevzuat_icindekiler mevzuat_madde_getir resmi_gazete_fihrist resmi_gazete_getir "
          "resmi_gazete_tara semantik_ara spk_bulten_icinde_ara status uyusmazlik_ara uyusmazlik_getir").split()
    # Öbür yargı çevrelerinin İngilizce fiilleri ve birleşik ucun kendi aracı.
    GENEL = {"search", "get", "list", "status", "fetch", "count", "browse", "recent", "resolve"}

    def test_ortak_sozcuk_yok(self):
        yasak = {s for ad in self.TR for s in ad.split("_")} | self.GENEL
        yasak |= {s[:-1] for s in yasak if s.endswith("i") and len(s) > 5}   # rehberi → rehber
        for arac in server.TUM_ARACLAR:
            if arac.name == "server_status":        # birleşik uç bunu listelemez, yerine kendi `status`u
                continue
            ortak = set(arac.name.split("_")) & yasak
            self.assertFalse(ortak, "tkgm_%s, başka bir aracın sözcüğünü taşıyor: %s" % (arac.name, ortak))

    def test_ad_tekil(self):
        adlar = [a.name for a in server.TUM_ARACLAR]
        self.assertEqual(len(adlar), len(set(adlar)))


class Manifesto(unittest.TestCase):
    def test_manifesto_rakamlari_ve_iletisim(self):
        with open(os.path.join(KOK, "docs", "MANIFESTO.md"), encoding="utf-8") as fh:
            metin = " ".join(fh.read().split())       # satır kırılması aramayı bozmasın
        for parca in (canli.ILETISIM, "açık kaynak", "ticari", "dakikada en çok 30", "Bilgi Teknolojileri",
                      "429", "TKGM_CANLI=0", "ArthurLegal-Tapu", "en az 2 saniye", "20 saniyeyi",
                      "3.000", "24 saat"):
            self.assertTrue(parca in metin, parca)    # assertIn bütün sayfayı dökerdi
        self.assertFalse("—" in metin)                # AL yazım tarzı: uzun tire yok

    def test_onay_karti(self):
        kart = canli.onay_karti()
        for parca in ("açık kaynaklı", "ticari olmayan", "dakikada en çok 30", canli.MANIFESTO, "evet / hayır"):
            self.assertIn(parca, kart, parca)


# --------------------------------------------------------------------------- #
class UctanUca(Sahte):
    def test_onay_karti_once_sonra_sorgu(self):
        y = self.cagir(server._t_parsel_sorgula, il="İstanbul", ilce="Kadıköy", mahalle="Deneme", ada=101, parsel=7)
        self.assertTrue(y["onay_gerekli"])
        self.assertIn("ticari olmayan", y["kart"])
        self.assertEqual(self.istek_sayisi(), 0, "onaysız istek gitti")
        y = self.cagir(server._t_parsel_sorgula, il="İstanbul", ilce="Kadıköy", mahalle="Deneme", ada=101, parsel=7,
                       onay=True)
        self.assertEqual((y["il"], y["ilce"], y["mahalle"], y["ada"], y["parsel_no"]),
                         ("İstanbul", "Kadıköy", "Deneme", "101", "7"))
        self.assertIn("canlı", y["kaynak"])
        self.assertIn("parsel_sorgu", y["baglantilar"])
        self.assertAlmostEqual(y["hesap_alani_m2"], y["tapu_alani_m2"], delta=25)
        # sonraki araçlar ref ile çalışır
        self.assertIn("olcek", self.cagir(server._t_kroki, ref=y["ref"], inline=True))
        self.assertEqual(self.cagir(server._t_geometri, ref=y["ref"])["kose_sayisi"], 4)

    def test_onay_karti_kapatilabilir(self):
        os.environ["TKGM_ONAY_KARTI"] = "0"
        y = self.cagir(server._t_parsel_sorgula, metin="İstanbul Kadıköy Deneme 101 ada 7 parsel")
        self.assertEqual(y["parsel_no"], "7")

    def test_kimlik_basligi_cerez_yok(self):
        self.cagir(server._t_parsel_sorgula, mahalle_id=5001, ada=101, parsel=7, onay=True)
        yol, basliklar = self.srv.istekler[-1]
        self.assertEqual(yol, "/api/parsel/5001/101/7")
        self.assertIn("ArthurLegal-Tapu/", basliklar["user-agent"])
        self.assertIn(canli.MANIFESTO, basliklar["user-agent"])
        for yok in ("cookie", "referer", "origin"):
            self.assertNotIn(yok, basliklar)

    def test_onbellek_ikinci_sorgu_tkgmye_gitmez(self):
        a = dict(il="istanbul", ilce="kadikoy", mahalle="deneme", ada=101, parsel=7, onay=True)
        self.cagir(server._t_parsel_sorgula, **a)
        once = self.istek_sayisi()
        self.assertEqual(once, 4)                      # il, ilçe, mahalle listesi + parsel
        y = self.cagir(server._t_parsel_sorgula, **a)
        self.assertEqual(self.istek_sayisi(), once)
        self.assertIn("önbellekten", y["kaynak"])
        self.cagir(server._t_parsel_sorgula, **dict(a, parsel=9))
        self.assertEqual(self.istek_sayisi(), once + 1)   # listeler önbellekte, yalnız parsel

    def test_bulunamadi_bir_saat_hatirlanir(self):
        a = dict(mahalle_id=5001, ada=101, parsel=999, onay=True)
        with self.assertRaises(McpError):
            server._t_parsel_sorgula(a)
        with self.assertRaises(McpError):
            server._t_parsel_sorgula(a)
        self.assertEqual(self.istek_sayisi(), 1)

    def test_belirsiz_mahalle_adaylari(self):
        y = self.cagir(server._t_parsel_sorgula, il="İstanbul", ilce="Kadıköy", mahalle="Yen", ada=1, parsel=2,
                       onay=True)
        self.assertEqual(y["belirsiz"], "mahalle")
        self.assertEqual({a["id"] for a in y["adaylar"]}, {5002, 5003})

    def test_konum_ve_yer_bul(self):
        y = self.cagir(server._t_yer_bul, sorgu="Deneme Parkı Kadıköy", onay=True)
        aday = y["adaylar"][0]
        self.assertIn("OpenStreetMap", y["atif"])
        p = self.cagir(server._t_konumdan_parsel, enlem=aday["enlem"], boylam=aday["boylam"], onay=True)
        self.assertEqual(p["parsel_no"], "7")
        with self.assertRaises(McpError):
            server._t_konumdan_parsel({"enlem": 29.03, "boylam": 40.99, "onay": True})   # ters sıra, ağa çıkmaz

    def test_tarama_deseni_tkgmye_gitmeden_durur(self):
        for n in range(1, 6):
            self.cagir(server._t_parsel_sorgula, mahalle_id=5001, ada=101, parsel=n, onay=True)
        once = self.istek_sayisi()
        with self.assertRaises(McpError) as h:
            server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": 6, "onay": True})
        self.assertIn("ardışık", str(h.exception))
        self.assertEqual(self.istek_sayisi(), once)

    def test_429_sonrasi_istek_gitmez(self):
        self.srv.mod = "429"
        with self.assertRaises(McpError) as h:
            server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": 7, "onay": True})
        self.assertIn("yavaşlamamızı istedi", str(h.exception))
        self.srv.mod = ""
        with self.assertRaises(McpError) as h:
            server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": 8, "onay": True})
        self.assertIn("bekliyor", str(h.exception))
        self.assertEqual(self.istek_sayisi(), 1)
        self.assertGreaterEqual(canli.TKGM.devre.kalan(), 119)   # Retry-After: 120

    def test_403_yirmi_dort_saat(self):
        self.srv.mod = "403"
        with self.assertRaises(McpError) as h:
            server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": 7, "onay": True})
        self.assertIn("24 saat", str(h.exception))
        self.srv.mod = ""
        with self.assertRaises(McpError):
            server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": 8, "onay": True})
        self.assertEqual(self.istek_sayisi(), 1)

    def test_json_olmayan_yanit_uc_kez_sonra_devre(self):
        self.srv.mod = "html"
        for n in (7, 8, 9):
            with self.assertRaises(McpError):
                server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": n, "onay": True})
        self.assertEqual(self.istek_sayisi(), 3)
        with self.assertRaises(McpError):
            server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": 10, "onay": True})
        self.assertEqual(self.istek_sayisi(), 3)

    def test_yonlendirme_izlenmez(self):
        self.srv.mod = "redirect"
        with self.assertRaises(McpError) as h:
            server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": 7, "onay": True})
        self.assertIn("yönlendirme", str(h.exception))
        self.assertEqual([y for y, _ in self.srv.istekler], ["/api/parsel/5001/101/7"])

    def test_sira_dolu_istek_gonderilmez(self):
        canli.TKGM = canli.Kapi("TKGM", 5.0, 0.5, 10000)
        self.cagir(server._t_parsel_sorgula, mahalle_id=5001, ada=101, parsel=7, onay=True)
        with self.assertRaises(McpError) as h:
            server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": 8, "onay": True})
        self.assertIn("sırası dolu", str(h.exception))
        self.assertEqual(self.istek_sayisi(), 1)

    def test_anahtar_kapali(self):
        os.environ["TKGM_CANLI"] = "0"
        with self.assertRaises(McpError) as h:
            server._t_parsel_sorgula({"mahalle_id": 5001, "ada": 101, "parsel": 7, "onay": True})
        self.assertIn("TKGM_CANLI=0", str(h.exception))
        self.assertEqual(self.istek_sayisi(), 0)

    def test_rapor_yerel_dosyalar(self):
        self.cagir(server._t_parsel_sorgula, il="İstanbul", ilce="Kadıköy", mahalle="Deneme", ada=101, parsel=7,
                   onay=True)
        y = self.cagir(server._t_parsel_raporu, metin="Kadıköy Deneme 101 ada 7 parsel", onay=True,
                       konu="onalim")
        md = y["rapor_md"]
        for parca in ("# Parsel raporu", "| Ada / Parsel | 101 / 7 |", "Parsel Sorgu: ", "## Hukuki işaretler",
                      "4721 Türk Medeni Kanunu", "## Sınırlar"):
            self.assertIn(parca, md, parca)
        for yol in y["dosyalar"].values():
            self.assertTrue(os.path.isfile(yol), yol)

    def test_paylasilan_uc_ref_kodu_ve_sayisiz_durum(self):
        os.environ["MCP_TRANSPORT"] = "http"
        y = self.cagir(server._t_parsel_sorgula, mahalle_id=5001, ada=101, parsel=7, onay=True)
        self.assertTrue(y["ref_kodu"].startswith("P1."))
        r = self.cagir(server._t_parsel_raporu, ref=y["ref_kodu"], kroki=True)
        self.assertNotIn("dosyalar", r)
        self.assertIn("<svg", r["kroki_svg"])
        durum = server._t_status({})["canli"]["tkgm"]
        self.assertNotIn("bugun_gonderilen", durum)
        self.assertIn("dogrulanmamis_uclar", durum)


class ArayuzFormu(Sahte):
    """Yerel arayüzün yapılandırılmış sorgu formu: seçim kutularını dolduran uçlar ve sayfanın kendisi."""

    def setUp(self):
        super().setUp()
        import tkgm_ui
        self.tkgm_ui = tkgm_ui
        self.ui = tkgm_ui.sunucu(server.TOOLS, 0)
        threading.Thread(target=self.ui.serve_forever, daemon=True).start()
        self.ui_kok = "http://127.0.0.1:%d" % self.ui.server_address[1]

    def tearDown(self):
        self.ui.shutdown()
        self.ui.server_close()
        super().tearDown()

    def al(self, yol, belirtec=True, **p):
        import urllib.error
        import urllib.request
        if belirtec:
            p["t"] = self.tkgm_ui.BELIRTEC
        adres = self.ui_kok + yol + ("?" + urllib.parse.urlencode(p) if p else "")
        try:
            with urllib.request.urlopen(adres, timeout=30) as y:
                return y.status, y.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def json_al(self, yol, **p):
        kod, govde = self.al(yol, **p)
        return kod, json.loads(govde)

    def parsel_istekleri(self):
        return [y for y, _ in self.srv.istekler if y.startswith("/api/parsel/")]

    def test_belirtecsiz_ret(self):
        self.assertEqual(self.al("/api/idari", belirtec=False)[0], 403)
        self.assertEqual(self.istek_sayisi(), 0)

    def test_onaydan_once_tkgmye_gitmez(self):
        kod, j = self.json_al("/api/idari")
        self.assertEqual(kod, 200)
        self.assertTrue(j["onay_gerekli"])
        self.assertIn("ticari olmayan", j["kart"])
        self.assertEqual(self.istek_sayisi(), 0)

    def test_listeler_kademeli_ve_sirali(self):
        _, j = self.json_al("/api/idari", onay=1)
        self.assertEqual(j["tur"], "il")
        self.assertEqual([k["ad"] for k in j["liste"]], ["Ankara", "İstanbul", "İzmir"])   # Türkçe sıralama
        _, j = self.json_al("/api/idari", onay=1, il=34)
        self.assertEqual((j["tur"], {k["id"] for k in j["liste"]}), ("ilce", {1001, 1002, 1003}))
        _, j = self.json_al("/api/idari", onay=1, ilce=1001)
        self.assertEqual(j["tur"], "mahalle")
        self.assertEqual([k["ad"] for k in j["liste"]][0], "Deneme")
        self.assertEqual(self.parsel_istekleri(), [])

    def test_gecersiz_kimlik_ret(self):
        for kotu in ("abc", "../x", "1;2", "-5", "1.5"):
            self.assertEqual(self.al("/api/idari", onay=1, il=kotu)[0], 400, kotu)
        self.assertEqual(self.istek_sayisi(), 0)

    def test_sor_metni_kutulara_cozulur_sorgu_atilmaz(self):
        _, j = self.json_al("/api/idari/coz", onay=1, metin="İstanbul Kadıköy Deneme 101 ada 7 parsel")
        self.assertTrue(j["ok"])
        self.assertEqual((j["il"]["id"], j["ilce"]["id"], j["mahalle"]["id"], j["ada"], j["parsel"]), (34, 1001, 5001, 101, 7))
        self.assertEqual(self.parsel_istekleri(), [])      # kutucukları doldurur, sorguyu kullanıcı başlatır

    def test_belirsiz_mahallede_il_ilce_dolu_gelir(self):
        self.json_al("/api/idari", onay=1)
        self.json_al("/api/idari", onay=1, il=34)
        _, j = self.json_al("/api/idari/coz", onay=1, metin="Kadıköy Yen 101 ada 8 parsel")
        b = j["belirsiz"]
        self.assertEqual((b["belirsiz"], b["il"]["id"], b["ilce"]["id"], b["ada"], b["parsel"]), ("mahalle", 34, 1001, 101, 8))
        self.assertEqual({a["id"] for a in b["adaylar"]}, {5002, 5003})

    def test_koordinat_metni_kutulara(self):
        _, j = self.json_al("/api/idari/coz", onay=1, metin="40.9902, 29.0344")
        self.assertEqual((j["enlem"], j["boylam"]), (40.9902, 29.0344))
        self.assertEqual(self.parsel_istekleri(), [])

    def test_sayfa_yapilandirilmis_formu_tasir(self):
        kod, sayfa = self.al("/", belirtec=False)
        self.assertEqual(kod, 200)
        for parca in ('id="f-il"', 'id="f-ilce"', 'id="f-mahalle"', 'id="f-ada"', 'id="f-parsel"', 'id="f-sorgula"',
                      'inputmode="numeric"', "otokontrol", "/api/idari/coz"):
            self.assertTrue(parca in sayfa, parca)
        self.assertFalse("canli-metin" in sayfa)            # eski serbest metin kutusu kalmadı


if __name__ == "__main__":
    unittest.main()
