"""Yerel arayüz sunucusunun kilitleri: Host denetimi, X-Tkgm başlığı, klasör dışına çıkamama."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402
import tkgm_ui  # noqa: E402
from test_offline import ornek_geojson  # noqa: E402


class Arayuz(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["TKGM_CIKTI"] = cls.tmp
        os.environ["TKGM_DOSYA_ERISIMI"] = "1"
        with open(os.path.join(cls.tmp, "a_kroki.svg"), "w", encoding="utf-8") as fh:
            fh.write("<svg xmlns='http://www.w3.org/2000/svg'/>")
        with open(os.path.join(cls.tmp, "gizli.txt"), "w", encoding="utf-8") as fh:
            fh.write("sir")
        cls.httpd = tkgm_ui.sunucu(server.TOOLS, 0)
        cls.kok = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.environ.pop("TKGM_CIKTI", None)
        os.environ.pop("TKGM_DOSYA_ERISIMI", None)

    def _iste(self, yol, govde=None, basliklar=None):
        req = urllib.request.Request(self.kok + yol, data=govde, headers=basliklar or {},
                                     method="POST" if govde is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def _cagir(self, arac, args, basliklar=None):
        h = {"Content-Type": "application/json", "X-Tkgm": "1"} if basliklar is None else basliklar
        kod, govde, _ = self._iste("/api/cagir", json.dumps({"arac": arac, "args": args}).encode(), h)
        return kod, json.loads(govde)

    def test_sayfa_ve_logo(self):
        kod, govde, _ = self._iste("/")
        self.assertEqual(kod, 200)
        self.assertIn("ArthurLegal", govde.decode("utf-8"))
        self.assertEqual(self._iste("/logo.png")[1][:4], b"\x89PNG")

    def test_baslik_olmadan_post_reddedilir(self):
        kod, j = self._cagir("rehber", {}, {"Content-Type": "application/json"})
        self.assertEqual(kod, 403)
        self.assertFalse(j["ok"])

    def test_yabanci_host_reddedilir(self):
        kod, _, _ = self._iste("/", basliklar={"Host": "kotu.example:80"})
        self.assertEqual(kod, 403)
        kod, _ = self._cagir("rehber", {}, {"Content-Type": "application/json", "X-Tkgm": "1", "Host": "kotu.example"})
        self.assertEqual(kod, 403)

    def test_arac_cagrisi_ve_ortak_bellek(self):
        kod, j = self._cagir("parsel_oku", {"icerik": ornek_geojson()})
        self.assertTrue(j["ok"])
        ref = j["sonuc"]["parseller"][0]["ref"]
        kod, j = self._cagir("kroki", {"ref": ref, "inline": True})
        self.assertIn("<svg", j["sonuc"]["svg"])
        _, govde, _ = self._iste("/api/parseller")
        self.assertIn(ref, [p["ref"] for p in json.loads(govde)["parseller"]])
        kod, j = self._cagir("geometri", {"ref": "yok"})
        self.assertFalse(j["ok"])                              # McpError arayüze hata olarak döner
        self.assertEqual(self._cagir("olmayan_arac", {})[0], 400)

    def test_dosya_sunumu_klasorden_cikamaz(self):
        kod, _, basliklar = self._iste("/dosya/a_kroki.svg")
        self.assertEqual(kod, 200)
        self.assertIn("sandbox", basliklar.get("Content-Security-Policy", ""))
        for yol in ("/dosya/gizli.txt", "/dosya/..%2Fgizli.txt", "/dosya/..%5C..%5Cwin.ini", "/dosya/%2E%2E/a_kroki.svg"):
            self.assertEqual(self._iste(yol)[0], 404, yol)

    def test_galeri_yalniz_izinli_uzantilar(self):
        _, govde, _ = self._iste("/api/dosyalar")
        adlar = [d["ad"] for d in json.loads(govde)["dosyalar"]]
        self.assertIn("a_kroki.svg", adlar)
        self.assertNotIn("gizli.txt", adlar)


if __name__ == "__main__":
    unittest.main()
