"""Ortak üst şerit (AL v3.3.1): güncelleme denetimi, Şimdi güncelle, sistem bilgisi, yenilikler, ortak tercihler.

AL kurulumu sahte bir KOK'la taklit edilir: KOK/bin/al.py, sözleşmedeki JSON'u basan küçük bir betiktir; hiçbir
şey indirilmez, GitHub'a bağlanılmaz.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
import tkgm_ui  # noqa: E402

SAHTE_AL = r'''
import json, os, sys, time
kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
veri = os.path.join(kok, "veri")
os.makedirs(veri, exist_ok=True)
args = sys.argv[1:]
with open(os.path.join(veri, "cagrilar.txt"), "a", encoding="utf-8") as f:
    f.write(" ".join(args) + "\n")
kilit = os.path.join(veri, "kilit")
if os.environ.get("SAHTE_AL_ESKI"):
    sys.stderr.write("usage: al.py guncelle [-h]\n")
    sys.exit(2)
if args[:1] == ["guncelle"] and "--denetle" in args:
    print("gunluk satiri")
    print(json.dumps({"durum": "yeni", "denetim": 1763190000, "onbellek": "--zorla" not in args,
                      "kurulu": {"al": "3.3.0", "tapu": "0.7.0"}, "son": {"al": "3.3.1", "tapu": "0.7.1"},
                      "yeni": ["al", "tapu"], "yenilikler": {"tr": ["Tapu: üst şerit"], "en": ["Tapu: top bar"]},
                      "baglanti": {"al": "https://github.com/beerbottle90/ArthurLegal#indir",
                                   "kotu": "https://kotu.example/indir"},
                      "guncelleme": {"calisiyor": os.path.exists(kilit), "son_sonuc": "paket güncel",
                                     "yeniden_baslat": True}}, ensure_ascii=False))
elif args[:1] == ["guncelle"] and "--tani" in args:
    print(json.dumps({"urun": "ArthurLegal", "surumler": {"al": "3.3.0", "tapu": "0.7.0"},
                      "isletim_sistemi": "Sahte OS", "mimari": "AMD64", "dil": "tr", "moduller": ["tapu"],
                      "son_hatalar": []}))
elif args == ["guncelle"]:
    open(kilit, "w").close()
    time.sleep(1.5)
    os.remove(kilit)
'''


class SahteKurulum(unittest.TestCase):
    def setUp(self):
        self.kok = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.kok, "bin"))
        os.makedirs(os.path.join(self.kok, "veri"))
        with open(os.path.join(self.kok, "bin", "al.py"), "w", encoding="utf-8") as fh:
            fh.write(SAHTE_AL)
        self._eski = os.environ.get("ARTHURLEGAL_KOK")
        os.environ["ARTHURLEGAL_KOK"] = self.kok
        tkgm_ui._GUNCELLEME["surec"] = None

    def tearDown(self):
        surec = tkgm_ui._GUNCELLEME["surec"]
        if surec is not None and surec.poll() is None:
            surec.wait(10)
        tkgm_ui._GUNCELLEME["surec"] = None
        os.environ.pop("SAHTE_AL_ESKI", None)
        if self._eski is None:
            os.environ.pop("ARTHURLEGAL_KOK", None)
        else:
            os.environ["ARTHURLEGAL_KOK"] = self._eski
        shutil.rmtree(self.kok, ignore_errors=True)

    def cagrilar(self):
        try:
            with open(os.path.join(self.kok, "veri", "cagrilar.txt"), encoding="utf-8") as fh:
                return [s.strip() for s in fh if s.strip()]
        except OSError:
            return []

    def tercih_dosyasi(self):
        with open(os.path.join(self.kok, "veri", "arayuz-tercihleri.json"), encoding="utf-8") as fh:
            return json.load(fh)


class Guncelleme(SahteKurulum):
    def test_denetim_yalniz_kendi_modulune_bakar_ve_baglantilari_suzer(self):
        v = tkgm_ui.guncelleme_denetle(zorla=True)
        self.assertTrue(v["ok"])
        self.assertFalse(v["al_yok"])
        self.assertEqual(v["modul"], {"ad": "tapu", "kurulu": "0.7.0", "son": "0.7.1", "yeni": True})
        self.assertEqual(v["baglanti"], {"al": "https://github.com/beerbottle90/ArthurLegal#indir"})
        self.assertEqual(v["yenilikler"]["tr"], ["Tapu: üst şerit"])           # alt süreç UTF-8 yazar
        self.assertEqual(self.cagrilar(), ["guncelle --denetle --json --zorla"])
        tkgm_ui.guncelleme_denetle()
        self.assertEqual(self.cagrilar()[-1], "guncelle --denetle --json")     # açılışta: önbellek, --zorla yok

    def test_eski_al_json_vermezse_hata_kaydi(self):
        os.environ["SAHTE_AL_ESKI"] = "1"
        v = tkgm_ui.guncelleme_denetle(zorla=True)
        self.assertEqual(v["durum"], "hata")
        self.assertIn("desteklemiyor", v["hata"])
        self.assertFalse(v["modul"]["yeni"])

    def test_simdi_guncelle_arka_planda_ve_ikinci_kez_baslamaz(self):
        r = tkgm_ui.guncelleme_baslat()
        self.assertEqual(r, {"ok": True, "baslatildi": True})
        self.assertTrue(tkgm_ui.guncelleme_baslat().get("zaten"))
        self.assertTrue(tkgm_ui.guncelleme_denetle()["calisiyor"])
        tkgm_ui._GUNCELLEME["surec"].wait(15)
        son = tkgm_ui.guncelleme_denetle()
        self.assertFalse(son["calisiyor"])
        self.assertEqual(son["surec_kodu"], 0)
        self.assertEqual(self.cagrilar().count("guncelle"), 1)

    def test_al_kendi_kilidiyle_calisirken_baslatilmaz(self):
        open(os.path.join(self.kok, "veri", "kilit"), "w").close()
        self.assertTrue(tkgm_ui.guncelleme_baslat().get("zaten"))
        self.assertNotIn("guncelle", self.cagrilar())

    def test_pencere_acmayan_surec(self):
        if os.name == "nt":
            self.assertEqual(tkgm_ui._surec_ayarlari()["creationflags"] & 0x08000000, 0x08000000)
            self.assertTrue(tkgm_ui._surec_ayarlari(arka_plan=True)["creationflags"] & 0x00000200)
        else:
            self.assertEqual(tkgm_ui._surec_ayarlari(), {})
            self.assertTrue(tkgm_ui._surec_ayarlari(arka_plan=True)["start_new_session"])

    def test_sistem_bilgisi(self):
        j = tkgm_ui.sistem_bilgisi()
        self.assertEqual(j["arayuz"]["ad"], "ArthurTapu")
        self.assertEqual(j["arayuz"]["surum"], server.__version__)
        self.assertEqual(j["tani"]["surumler"]["al"], "3.3.0")


class Tercihler(SahteKurulum):
    def test_yalniz_kendi_anahtarini_yazar(self):
        with open(os.path.join(self.kok, "veri", "arayuz-tercihleri.json"), "w", encoding="utf-8") as fh:
            json.dump({"dil": "az", "gorulen_yenilikler": {"mask": "1.4.0"}, "baska": 1}, fh)
        r = tkgm_ui.tercih_yaz({"yazi": "buyuk", "karsitlik": True, "gorulen": "0.7.0", "bilinmeyen": "x"})
        self.assertTrue(r["ok"])
        self.assertEqual(self.tercih_dosyasi(), {"dil": "az", "yazi": "buyuk", "karsitlik": True, "baska": 1,
                                                 "gorulen_yenilikler": {"mask": "1.4.0", "tapu": "0.7.0"}})
        self.assertEqual(tkgm_ui.tercihler(), {"kok": True, "dil": "az", "yazi": "buyuk", "karsitlik": True,
                                               "gorulen": "0.7.0"})
        self.assertFalse(os.path.exists(os.path.join(self.kok, "veri", "arayuz-tercihleri.json.tmp")))

    def test_gecersiz_deger_yazilmaz(self):
        self.assertFalse(tkgm_ui.tercih_yaz({"dil": "de", "yazi": "dev", "karsitlik": "evet"})["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.kok, "veri", "arayuz-tercihleri.json")))

    def test_dil_okuma_sirasi(self):
        self.assertEqual(tkgm_ui.tercihler()["dil"], "tr")
        with open(os.path.join(self.kok, "veri", "durum.json"), "w", encoding="utf-8") as fh:
            json.dump({"dil": "en", "masa_dili": "az"}, fh)
        self.assertEqual(tkgm_ui.tercihler()["dil"], "az")
        tkgm_ui.tercih_yaz({"dil": "en"})
        self.assertEqual(tkgm_ui.tercihler()["dil"], "en")

    def test_yenilikler_kurulu_paketten_en_cok_bes(self):
        with open(os.path.join(self.kok, "aktif.txt"), "w", encoding="utf-8") as fh:
            fh.write("3.3.1\n")
        os.makedirs(os.path.join(self.kok, "surumler", "3.3.1"))
        with open(os.path.join(self.kok, "surumler", "3.3.1", "yenilikler.json"), "w", encoding="utf-8") as fh:
            json.dump({"tr": ["1", "2", "3", "4", "5", "6"], "az": ["bir"], "xx": ["?"]}, fh)
        j = tkgm_ui.yenilikler()
        self.assertEqual(j["surum"], "3.3.1")
        self.assertEqual(j["yenilikler"], {"tr": ["1", "2", "3", "4", "5"], "az": ["bir"]})


class DurumNoktasi(unittest.TestCase):
    """Şeritteki TKGM noktası ölçülmüş bilgiden beslenir: istek gitmeden yanıt ya da arıza kaydı yoktur."""

    def test_son_yanit_ve_son_ariza(self):
        import tkgm_canli as canli
        d = canli.Devre()
        self.assertEqual(d.durum(), {"durum": "çalışıyor"})
        d.basari()
        self.assertIn("son_basari", d.durum())
        d.hata("ulaşılamadı")
        self.assertEqual(d.durum()["son_ariza"]["neden"], "ulaşılamadı")
        d.reddedildi("HTTP 403: erişim reddedildi")
        self.assertEqual(d.durum()["durum"], "duraklatıldı")
        self.assertGreater(d.durum()["kalan_sn"], 0)


class KurulumYok(unittest.TestCase):
    def setUp(self):
        self.bos = tempfile.mkdtemp()
        self._eski = os.environ.get("ARTHURLEGAL_KOK")
        os.environ["ARTHURLEGAL_KOK"] = self.bos

    def tearDown(self):
        if self._eski is None:
            os.environ.pop("ARTHURLEGAL_KOK", None)
        else:
            os.environ["ARTHURLEGAL_KOK"] = self._eski
        shutil.rmtree(self.bos, ignore_errors=True)

    def test_dugme_yalniz_kurulum_sayfasini_acar(self):
        v = tkgm_ui.guncelleme_denetle()
        self.assertTrue(v["al_yok"])
        self.assertTrue(v["kurulum_sayfasi"].startswith("https://github.com/beerbottle90/ArthurLegal#"))
        r = tkgm_ui.guncelleme_baslat()
        self.assertFalse(r["ok"])
        self.assertTrue(r["al_yok"])
        self.assertEqual(tkgm_ui.tercihler()["kok"], False)
        self.assertEqual(tkgm_ui.tercih_yaz({"dil": "en"}), {"ok": True, "kok": False})
        self.assertTrue(tkgm_ui.sistem_bilgisi()["al_yok"])


class SayfaVeUclar(SahteKurulum):
    def setUp(self):
        super().setUp()
        self.httpd = tkgm_ui.sunucu(server.TOOLS, 0)
        self.adres = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def iste(self, yol, govde=None, belirtec=True):
        basliklar = {}
        if belirtec and govde is None and yol.startswith("/api/"):
            yol += ("&" if "?" in yol else "?") + "t=" + tkgm_ui.BELIRTEC
        if govde is not None:
            basliklar = {"Content-Type": "application/json", "X-Tkgm": tkgm_ui.BELIRTEC}
        req = urllib.request.Request(self.adres + yol, data=None if govde is None else json.dumps(govde).encode(),
                                     headers=basliklar, method="GET" if govde is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_sayfa_sozlukleri_ve_tercihleri_tasir(self):
        kod, govde = self.iste("/")
        sayfa = govde.decode("utf-8")
        self.assertEqual(kod, 200)
        self.assertNotIn("__TKGM_DIL__", sayfa)
        self.assertNotIn("__TKGM_TERCIH__", sayfa)
        dil = json.loads(sayfa.split('<script type="application/json" id="tkgm-dil">')[1].split("</script>")[0])
        self.assertEqual(dil["en"]["Güncellemeleri denetle"], "Check for updates")
        tercih = json.loads(sayfa.split('<script type="application/json" id="tkgm-tercih">')[1].split("</script>")[0])
        self.assertTrue(tercih["kok"])
        self.assertEqual(tercih["surum"], server.__version__)

    def test_uclar_belirtec_ister(self):
        for yol in ("/api/guncelleme", "/api/tani", "/api/yenilikler", "/api/tercihler"):
            self.assertEqual(self.iste(yol, belirtec=False)[0], 403, yol)
            self.assertEqual(self.iste(yol)[0], 200, yol)
        req = urllib.request.Request(self.adres + "/api/guncelleme/baslat", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as hata:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(hata.exception.code, 403)
        self.assertNotIn("guncelle", self.cagrilar())

    def test_tercih_ve_guncelleme_uclari(self):
        kod, govde = self.iste("/api/tercihler", {"dil": "en"})
        self.assertEqual((kod, json.loads(govde)["dil"]), (200, "en"))
        self.assertEqual(self.iste("/api/tercihler", {"dil": "xx"})[0], 400)
        kod, govde = self.iste("/api/guncelleme?zorla=1")
        self.assertTrue(json.loads(govde)["modul"]["yeni"])
        kod, govde = self.iste("/api/guncelleme/baslat", {})
        self.assertTrue(json.loads(govde)["baslatildi"])
        tkgm_ui._GUNCELLEME["surec"].wait(15)
        time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
