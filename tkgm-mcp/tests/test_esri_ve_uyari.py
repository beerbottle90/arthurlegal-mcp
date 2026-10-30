"""Esri anahtarının yolu, 3B uyarısının yüzeyleri ve "arayüzde aç" bağlantısı (ağsız).

Anahtar: kullanıcının kendi ArcGIS anahtarıdır. Diske yalnız ayar klasörüne yazılır; üretilen
dosyada ve araç yanıtında (Claude'un gördüğü) GÖRÜNMEZ. Yalnız tarayıcıya giden yolda
(/api/cagir, /dosya) sayfaya yerleşir.

Uyarı: "3D görselinin gerçek veriye dayanmadığı, AI generated tahmin olduğu, fikir verme amaçlı
oluşturulduğu uyarısı repoya da arayüze de girsin" (kullanıcı, 21.09.2026). Tek kaynak
tkgm_harita.UYARI_3D; burada her yüzeyde durduğu denetlenir.

Görüntüleme belirteci (`?g=`): yalnız GET /dosya açar; /api'ye geçmez.
"""

import html
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402
import tkgm_bina as bina  # noqa: E402
import tkgm_harita as harita  # noqa: E402
import tkgm_parsel as parseller  # noqa: E402
import tkgm_ui  # noqa: E402
from mcpcore import McpError  # noqa: E402
from test_bina import _duz_izgara, ornek_bina_verisi  # noqa: E402
from test_offline import ornek_geojson  # noqa: E402

ANAHTAR = "AAPTsinama" + "Ab9_-." * 12
KOK_DIZIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def iste(kok, yol, govde=None, basliklar=None):
    req = urllib.request.Request(kok + yol, data=govde, headers=basliklar or {},
                                 method="POST" if govde is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _oku(*parca):
    with open(os.path.join(KOK_DIZIN, *parca), encoding="utf-8") as fh:
        return fh.read()


class _Ortam:
    """TKGM_CIKTI / TKGM_AYAR / TKGM_DOSYA_ERISIMI'yi geçici klasöre çevirir, sonra geri koyar."""
    ADLAR = ("TKGM_CIKTI", "TKGM_AYAR", "TKGM_DOSYA_ERISIMI", "TKGM_ESRI_ANAHTARI")

    def kur(self):
        self.tmp = tempfile.mkdtemp(prefix="tkgm-esri-")
        self.eski = {k: os.environ.get(k) for k in self.ADLAR}
        os.environ.pop("TKGM_ESRI_ANAHTARI", None)
        os.environ["TKGM_CIKTI"] = os.path.join(self.tmp, "cikti")
        os.environ["TKGM_AYAR"] = os.path.join(self.tmp, "ayar")
        os.environ["TKGM_DOSYA_ERISIMI"] = "1"
        tkgm_ui._GORUNTU["belirtec"] = None

    def boz(self):
        for k, v in self.eski.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        tkgm_ui._GORUNTU["belirtec"] = None
        shutil.rmtree(self.tmp, ignore_errors=True)


class _Sunucu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ortam = _Ortam()
        cls.ortam.kur()
        cls.httpd = tkgm_ui.sunucu(server.TOOLS, 0)
        cls.kok = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        tkgm_ui._ADRES["kendi"] = None
        cls.ortam.boz()

    def _api(self, yol, govde=None):
        h = {"X-Tkgm": tkgm_ui.BELIRTEC}
        if govde is not None:
            h["Content-Type"] = "application/json"
            govde = json.dumps(govde).encode("utf-8")
        kod, cevap, _ = iste(self.kok, yol, govde, h)
        return kod, json.loads(cevap)

    def _ref(self):
        _, j = self._api("/api/cagir", {"arac": "parsel_oku", "args": {"icerik": ornek_geojson()}})
        return j["sonuc"]["parseller"][0]["ref"]


class EsriAnahtariHttp(_Sunucu):
    def setUp(self):
        tkgm_ui.esri_anahtari_kaydet(None)
        self.sina = mock.patch.object(tkgm_ui, "esri_sina", return_value=True)
        self.sinama = self.sina.start()
        self.addCleanup(self.sina.stop)

    def test_ayar_anahtari_kaydeder_ama_geri_vermez(self):
        kod, j = self._api("/api/ayar")
        self.assertEqual((kod, j["esri"]), (200, False))
        kod, j = self._api("/api/ayar", {"esri_anahtari": "  " + ANAHTAR + "\n"})
        self.assertEqual((kod, j["ok"], j["esri"]), (200, True, True))
        self.sinama.assert_called_once_with(ANAHTAR)
        kod, j = self._api("/api/ayar")
        self.assertTrue(j["esri"])
        self.assertNotIn(ANAHTAR, json.dumps(j))
        # Ayar klasöründe durur; çıktı klasörü (ve dolayısıyla /dosya, galeri) onu görmez.
        self.assertEqual(tkgm_ui.esri_anahtari(), ANAHTAR)
        cikti = os.environ["TKGM_CIKTI"]
        for kok, _, adlar in os.walk(cikti) if os.path.isdir(cikti) else ():
            for ad in adlar:
                with open(os.path.join(kok, ad), encoding="utf-8", errors="replace") as fh:
                    self.assertNotIn(ANAHTAR, fh.read())
        self.assertEqual(iste(self.kok, "/dosya/esri_anahtari.txt?t=" + tkgm_ui.BELIRTEC)[0], 404)
        kod, j = self._api("/api/ayar", {"esri_anahtari": ""})
        self.assertEqual((kod, j["esri"]), (200, False))
        self.assertIsNone(tkgm_ui.esri_anahtari())

    def test_gecersiz_ya_da_reddedilen_anahtar_kaydedilmez(self):
        for kotu in ("kisa", ANAHTAR + "</script>", ANAHTAR + '"', 12345):
            kod, j = self._api("/api/ayar", {"esri_anahtari": kotu})
            self.assertEqual(kod, 400, kotu)
            self.assertFalse(j["ok"])
        self.sinama.return_value = False
        kod, j = self._api("/api/ayar", {"esri_anahtari": ANAHTAR})
        self.assertEqual(kod, 400)
        self.assertIn("reddetti", j["hata"])
        self.assertIsNone(tkgm_ui.esri_anahtari())
        self.sinama.return_value = None                     # ağ yok: kaydedilir ama söylenir
        kod, j = self._api("/api/ayar", {"esri_anahtari": ANAHTAR})
        self.assertEqual((kod, j["sinanamadi"]), (200, True))

    def test_ayar_belirtecsiz_reddedilir_ve_ortam_degiskeni_ezilmez(self):
        kod, _, _ = iste(self.kok, "/api/ayar", json.dumps({"esri_anahtari": ANAHTAR}).encode(),
                         {"Content-Type": "application/json"})
        self.assertEqual(kod, 403)
        self.assertEqual(iste(self.kok, "/api/ayar")[0], 403)
        os.environ["TKGM_ESRI_ANAHTARI"] = ANAHTAR
        try:
            kod, j = self._api("/api/ayar", {"esri_anahtari": ""})
            self.assertEqual(kod, 409)
            kod, j = self._api("/api/ayar")
            self.assertEqual((j["esri"], j["esri_kaynak"]), (True, "ortam"))
        finally:
            os.environ.pop("TKGM_ESRI_ANAHTARI", None)

    def test_anahtar_yalniz_tarayiciya_giden_yolda_yerlesir(self):
        ref = self._ref()
        # Anahtarsız: işaret durur, uydu katmanı yok.
        _, j = self._api("/api/cagir", {"arac": "harita", "args": {"ref": ref, "inline": True}})
        self.assertIn(harita.ESRI_ISARETI, j["sonuc"]["html"])
        tkgm_ui.esri_anahtari_kaydet(ANAHTAR)
        # Sayfanın yolu: yerleşir.
        _, j = self._api("/api/cagir", {"arac": "harita", "args": {"ref": ref, "inline": True}})
        self.assertIn('ESRI="%s"' % ANAHTAR, j["sonuc"]["html"])
        self.assertNotIn(harita.ESRI_ISARETI, j["sonuc"]["html"])
        # Claude'un yolu (MCP aracı doğrudan): ne satır içi yanıtta ne yazılan dosyada.
        satir_ici = server._t_harita({"ref": ref, "inline": True})
        self.assertNotIn(ANAHTAR, satir_ici)
        self.assertIn(json.dumps(harita.ESRI_ISARETI)[1:-1], satir_ici)
        dosyali = json.loads(server._t_harita({"ref": ref}))
        with open(dosyali["dosya"], encoding="utf-8") as fh:
            diskteki = fh.read()
        self.assertNotIn(ANAHTAR, diskteki)
        self.assertIn(harita.ESRI_ISARETI, diskteki)
        # /dosya: tarayıcıya giderken yerleşir, diskteki dosya değişmez.
        kod, govde, _ = iste(self.kok, "/dosya/%s?t=%s" % (os.path.basename(dosyali["dosya"]), tkgm_ui.BELIRTEC))
        self.assertEqual(kod, 200)
        self.assertIn('ESRI="%s"' % ANAHTAR, govde.decode("utf-8"))
        with open(dosyali["dosya"], encoding="utf-8") as fh:
            self.assertEqual(fh.read(), diskteki)

    def test_sayfaya_uyari_tek_kaynaktan_gomulur(self):
        _, govde, basliklar = iste(self.kok, "/")
        sayfa = govde.decode("utf-8")
        self.assertNotIn("__TKGM_UYARI_3D__", sayfa)
        self.assertIn('<meta name="tkgm-uyari-3d" content="%s">' % html.escape(harita.UYARI_3D, quote=True), sayfa)


class KaroErisimi(_Sunucu):
    """OSM karoları: yalnız köken gider (strict-origin); CSP sayfaların yüklediği adresleri tam listeler."""

    def test_referer_politikasi_koken_gonderir(self):
        _, _, basliklar = iste(self.kok, "/")
        self.assertEqual(basliklar.get("Referrer-Policy"), "strict-origin")
        ref = self._ref()
        dosya = json.loads(server._t_harita({"ref": ref}))["dosya"]
        _, govde, b = iste(self.kok, "/dosya/%s?t=%s" % (os.path.basename(dosya), tkgm_ui.BELIRTEC))
        self.assertEqual(b.get("Referrer-Policy"), "strict-origin")
        self.assertEqual(b.get("Content-Security-Policy"), "sandbox allow-scripts")
        self.assertIn("referrerPolicy:V.referrer", govde.decode("utf-8"))
        self.assertIn('"referrer":"strict-origin"', govde.decode("utf-8"))

    def test_csp_harita_ve_3d_adreslerini_tam_listeler(self):
        _, _, basliklar = iste(self.kok, "/")
        csp = basliklar.get("Content-Security-Policy")
        self.assertEqual(csp, tkgm_ui.csp())
        for adres in ("https://cdnjs.cloudflare.com", "https://tile.openstreetmap.org",
                      "https://cdn.jsdelivr.net", "https://ibasemaps-api.arcgis.com"):
            self.assertIn(adres, csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("google", csp.lower())


class GoruntuBaglantisi(_Sunucu):
    """`arayuzde_ac`: yalnız o dosyayı açar, /api'ye geçmez, başka sitenin sayfasına gömülemez."""

    def setUp(self):
        tkgm_ui._ADRES["kendi"] = self.kok + "/"
        self.addCleanup(tkgm_ui._ADRES.__setitem__, "kendi", None)
        self.ref = self._ref()

    def test_arac_ciktisi_baglanti_tasir_ve_acar(self):
        out = json.loads(server._t_harita({"ref": self.ref}))
        bag = out["arayuzde_ac"]
        self.assertTrue(bag.startswith(self.kok + "/dosya/"))
        self.assertNotIn(tkgm_ui.BELIRTEC, bag)
        kod, govde, _ = iste("", bag)
        self.assertEqual(kod, 200)
        self.assertIn("leaflet", govde.decode("utf-8"))
        # Sohbetteki bağlantıya tıklama: başka siteden üst düzey açılış geçer, gömme geçmez.
        gezinti = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        self.assertEqual(iste("", bag, None, gezinti)[0], 200)
        gomme = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "iframe"}
        self.assertEqual(iste("", bag, None, gomme)[0], 403)

    def test_goruntu_belirteci_api_acmaz(self):
        g = tkgm_ui.goruntu_belirteci()
        self.assertNotEqual(g, tkgm_ui.BELIRTEC)
        for yol in ("/api/parseller", "/api/dosyalar", "/api/ayar", "/api/cevaplar"):
            self.assertEqual(iste(self.kok, yol + "?g=" + g)[0], 403, yol)
        kod, _, _ = iste(self.kok, "/api/cagir?g=" + g, json.dumps({"arac": "baslangic", "args": {}}).encode(),
                         {"Content-Type": "application/json"})
        self.assertEqual(kod, 403)
        self.assertEqual(iste(self.kok, "/dosya/yok.html?g=yanlis-belirtec-yanlis-belirtec")[0], 403)

    def test_belirtec_ayar_klasorunde_kalici(self):
        g = tkgm_ui.goruntu_belirteci()
        with open(os.path.join(tkgm_ui.ayar_klasoru(), "goruntu_belirteci.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), g)
        tkgm_ui._GORUNTU["belirtec"] = None                  # yeni süreç: aynı belirteç okunur
        self.assertEqual(tkgm_ui.goruntu_belirteci(), g)

    def test_kroki_ve_3d_baglanti_alir_disa_aktarim_almaz(self):
        self.assertIn("arayuzde_ac", json.loads(server._t_kroki({"ref": self.ref})))
        self.assertNotIn("arayuzde_ac", json.loads(server._t_disa_aktar({"ref": self.ref, "bicim": "kml"})))

    def test_arayuz_kapaliyken_baglanti_yok(self):
        tkgm_ui._ADRES["kendi"] = None
        self.assertNotIn("arayuzde_ac", json.loads(server._t_harita({"ref": self.ref})))
        self.assertIsNone(tkgm_ui.arayuz_baglantisi(os.path.join(tempfile.gettempdir(), "x_harita.html")))

    def test_saglik_belirtecsiz_ve_verisiz(self):
        kod, govde, _ = iste(self.kok, "/saglik")
        self.assertEqual((kod, json.loads(govde)), (200, {"ok": True, "uygulama": "tkgm-ui"}))


class AraziAraci(unittest.TestCase):
    """server._t_arazi_3d: DEM ve bina ağ çağrıları sahteyle değiştirilir."""

    def setUp(self):
        self.ortam = _Ortam()
        self.ortam.kur()
        self.addCleanup(self.ortam.boz)
        out = json.loads(server._t_parsel_oku({"icerik": ornek_geojson()}))
        self.ref = out["parseller"][0]["ref"]
        (self.p,) = parseller.oku(ornek_geojson())
        izgara = _duz_izgara(self.p)
        self.dem = mock.patch.object(server.harita, "dem_izgara", return_value=izgara)
        self.bina = mock.patch.object(server.bina, "binalar", return_value=ornek_bina_verisi(self.p))
        self.dem_sahte, self.bina_sahte = self.dem.start(), self.bina.start()
        self.addCleanup(self.dem.stop)
        self.addCleanup(self.bina.stop)

    def test_cikti_uyariyi_ve_bina_ozetini_tasir(self):
        out = json.loads(server._t_arazi_3d({"ref": self.ref, "kat": 5}))
        self.assertEqual(out["uyari_3d"], harita.UYARI_3D)
        self.assertEqual(out["binalar"]["cizilen"], 4)
        self.assertEqual(out["binalar"]["parsel_binasi"]["kat"], 5)
        self.assertEqual(out["binalar"]["kip"], "kullanici")
        with open(out["dosya"], encoding="utf-8") as fh:
            sayfa = fh.read()
        self.assertIn(html.escape(harita.UYARI_3D), sayfa)
        self.assertIn(harita.ESRI_ISARETI, sayfa)                 # anahtar dosyaya yazılmaz
        self.assertIn("Binalar (tahmini kütle)", sayfa)
        # Uyarı şeridi kartın DIŞINDA: kart katlansa da görünür.
        self.assertLess(sayfa.index("</details></div>"), sayfa.index('<div class="uyari3d"'))

    def test_gecersiz_kat_aga_cikmadan_reddedilir(self):
        for kat in (0, 81, "beş", True, 2.5):
            with self.assertRaises(McpError, msg=repr(kat)):
                server._t_arazi_3d({"ref": self.ref, "kat": kat})
        self.dem_sahte.assert_not_called()
        self.bina_sahte.assert_not_called()

    def test_bina_verisi_gelmezse_arazi_yine_cizilir(self):
        self.bina_sahte.side_effect = bina.BinaHatasi("ağ yok")
        out = json.loads(server._t_arazi_3d({"ref": self.ref}))
        self.assertNotIn("binalar", out)
        self.assertTrue(any("Binalar çizilmedi" in u for u in out["uyari"]))
        self.assertEqual(out["uyari_3d"], harita.UYARI_3D)        # binasız sahne de tahmindir

    def test_binalar_false_ag_cagirmaz(self):
        out = json.loads(server._t_arazi_3d({"ref": self.ref, "binalar": False}))
        self.bina_sahte.assert_not_called()
        self.assertNotIn("binalar", out)


class UyariYuzeyleri(unittest.TestCase):
    """Depo ve arayüz yüzeyleri: metni biri silerse test düşer."""

    def test_uyari_metninin_ozu(self):
        for ifade in ("gerçek (ölçülmüş ya da resmî) veriye dayanmaz", "Yapay zekâ", "TAHMİNDİR", "fikir vermek"):
            self.assertIn(ifade, harita.UYARI_3D)

    def test_readme_birebir_tasir(self):
        self.assertIn(harita.UYARI_3D, _oku("README.md"))

    def test_rehber_ve_talimatlar_aktarmayi_soyler(self):
        rehber = _oku("docs", "tkgm-mcp-rehberi.md")
        self.assertIn("uyari_3d", rehber)
        self.assertIn("TAHMİNDİR", rehber)
        for metin in (server.INSTRUCTIONS, server.REHBER):
            self.assertIn("uyari_3d", metin)
            self.assertIn("arayuzde_ac", metin)
        arac = next(t for t in server.TUM_ARACLAR if t.name == "arazi_3d")
        self.assertIn("uyari_3d", arac.description)
        self.assertIn("TAHMİNİ", arac.description)

    def test_arayuz_3d_sekmesinde_gosterir(self):
        sayfa = _oku("ui", "index.html")
        self.assertIn('<meta name="tkgm-uyari-3d" content="__TKGM_UYARI_3D__">', sayfa)
        govde = sayfa[sayfa.index("  async uc(kap) {"):sayfa.index("  async hukuk(kap) {")]
        self.assertGreaterEqual(govde.count("uyari3d()"), 2)          # getirmeden önce ve sonra

    def test_google_goruntusu_kullanilmaz(self):
        for modul in ("tkgm_harita.py", "tkgm_bina.py", "tkgm_ui.py"):
            kaynak = _oku(modul)
            for yasak in ("maps.googleapis.com", "tile.googleapis.com", "mt0.google", "khms"):
                self.assertNotIn(yasak, kaynak, modul)


if __name__ == "__main__":
    unittest.main()
