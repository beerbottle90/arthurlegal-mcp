"""KVKK kaynağı: eksik sertifika zinciri onarımı ve erişilemeyen kaynağın UYARI cevabı (issue #78).

Ağa çıkmaz. 17.11.2026'dan beri www.kvkk.gov.tr yalnız yaprak sertifikasını gönderiyor; KVKK istemcisi eksik ara
sertifikayı yalnız kendi deposuna ekler (net.chain_context). Bu testler şunları kanıtlar:

* eklenen sertifika, kodda yazılı olan GlobalSign ara sertifikasının ta kendisidir (SHA-256);
* doğrulama açık kalır: ad denetimi, CERT_REQUIRED, kısmi zincir kapalı (kök hâlâ sistem deposundan gelmeli);
* ek güven başka hiçbir kaynağın istemcisine geçmez;
* KVKK okunamazsa araçlar boş liste değil, "UYARI: veri çekilemedi, teyidiniz gerekli: <adres>" döndürür.

Çalıştırma: python -m unittest discover -s tests -p test_kvkk_kaynak.py -v
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import sys
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import net  # noqa: E402
from net import Http, HttpError  # noqa: E402
from sources import bddk, kvkk, resmi_gazete  # noqa: E402

# kvkk.py'deki açıklamayla aynı değer: http://secure.globalsign.com/cacert/gsgccr46ovtlsca2025.crt, 24.11.2026.
GLOBALSIGN_SHA256 = "D160E2DE4E56CB10B66C1CB0ADCB79CF93C78DCD9DB30C2018220262C04063F9"
LISTE = "https://www.kvkk.gov.tr/Icerik/5406/kurul-karar-ozetleri"
TLS_HATASI = b"TLS certificate of this upstream could not be verified"


def _konu(sertifika):
    return {k: v for alan in sertifika.get("subject", ()) for k, v in alan}


class AraSertifika(unittest.TestCase):
    def test_parmak_izi_kodda_yazanla_ayni(self):
        der = ssl.PEM_cert_to_DER_cert(kvkk._GLOBALSIGN_GCC_R46_OV_2025)
        self.assertEqual(hashlib.sha256(der).hexdigest().upper(), GLOBALSIGN_SHA256)

    def test_kvkk_istemcisi_dogrulamayi_kapatmaz(self):
        ctx = kvkk._http.ssl_context
        self.assertIsNotNone(ctx)
        self.assertTrue(kvkk._http.verify)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        if hasattr(ssl, "VERIFY_X509_PARTIAL_CHAIN"):
            self.assertFalse(ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)
        adlar = {_konu(c).get("commonName") for c in ctx.get_ca_certs()}
        self.assertIn("GlobalSign GCC R46 OV TLS CA 2025", adlar)

    def test_ek_guven_yalniz_kvkk_alan_adinda(self):
        self.assertEqual(kvkk._http.allowed_domains, frozenset({"kvkk.gov.tr"}))
        self.assertFalse(kvkk._http.allowed("https://www.resmigazete.gov.tr/"))
        for istemci in (bddk._http, resmi_gazete._http):
            ctx = istemci.ssl_context
            adlar = {_konu(c).get("commonName") for c in ctx.get_ca_certs()} if ctx else set()
            self.assertNotIn("GlobalSign GCC R46 OV TLS CA 2025", adlar)

    def test_dogrulamasiz_istemciye_ara_sertifika_verilmez(self):
        with self.assertRaises(ValueError):
            Http("https://www.kvkk.gov.tr", verify=False, intermediates=kvkk._GLOBALSIGN_GCC_R46_OV_2025)

    def test_python_313_kismi_zinciri_de_kapatilir(self):
        ctx = net.chain_context(kvkk._GLOBALSIGN_GCC_R46_OV_2025)
        if hasattr(ssl, "VERIFY_X509_PARTIAL_CHAIN"):
            self.assertFalse(ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)


def _dogrulanamadi(*_a, **_k):
    raise urllib.error.URLError(ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"))


class TlsYedegi(unittest.TestCase):
    """HTTP_TLS_FALLBACK=1 açık olsa da ara sertifikalı KVKK istemcisi doğrulamasız bağlantıya düşmez (QA G1)."""

    def setUp(self):
        self._eski = net.TLS_FALLBACK
        net.TLS_FALLBACK = True

    def tearDown(self):
        net.TLS_FALLBACK = self._eski

    def test_kvkk_istemcisi_dogrulamali_kalir_ve_uyari_doner(self):
        ctx = kvkk._http.ssl_context
        with mock.patch.object(kvkk._http.opener, "open", _dogrulanamadi):
            with mock.patch.object(Http, "_degrade_tls", side_effect=AssertionError("yedek yola girildi")):
                out = kvkk.search({"query": "veri"})
        self.assertTrue(kvkk._http.verify)
        self.assertIs(kvkk._http.ssl_context, ctx)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertTrue(out["unavailable"])
        self.assertIn("TLS certificate", out["error"])
        self.assertTrue(out["uyari"].startswith("UYARI: veri çekilemedi, teyidiniz gerekli: https://www.kvkk.gov.tr/"))

    def test_dogrudan_cagrilsa_da_dusurmez(self):
        h = Http("https://www.kvkk.gov.tr", intermediates=kvkk._GLOBALSIGN_GCC_R46_OV_2025)
        with self.assertRaises(HttpError):
            h._degrade_tls("https://www.kvkk.gov.tr/")
        self.assertTrue(h.verify)
        self.assertEqual(h.ssl_context.verify_mode, ssl.CERT_REQUIRED)

    def test_ara_sertifikasiz_istemcide_kacis_kapisi_aynen_durur(self):
        # Karşı deneme: operatörün kaçış kapısı öteki kaynaklarda değişmedi.
        h = Http("https://www.bddk.org.tr")
        cagri = []
        with mock.patch.object(h.opener, "open", _dogrulanamadi), \
                mock.patch.object(Http, "_degrade_tls", lambda self, url: (cagri.append(url),
                                                                           setattr(self, "opener", _Bos()))):
            h.get("/x")
        self.assertEqual(len(cagri), 1)

    def test_ortam_degiskeniyle_ayri_surecte(self):
        # Ortam değişkeni net.py yüklenirken okunur; gerçek ayarı görmek için temiz bir süreç.
        betik = (
            "import json, ssl, sys, urllib.error\n"
            "sys.path.insert(0, %r)\n"
            "import net\n"
            "from sources import kvkk\n"
            "def hata(*a, **k):\n"
            "    raise urllib.error.URLError(ssl.SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] x'))\n"
            "kvkk._http.opener.open = hata\n"
            "out = kvkk.search({'query': 'veri'})\n"
            "print(json.dumps({'yedek': net.TLS_FALLBACK, 'verify': kvkk._http.verify,\n"
            "                  'mod': int(kvkk._http.ssl_context.verify_mode), 'ad': kvkk._http.ssl_context.check_hostname,\n"
            "                  'unavailable': out.get('unavailable'), 'uyari': out.get('uyari', '')}))\n"
        ) % os.path.dirname(HERE)
        ortam = dict(os.environ, HTTP_TLS_FALLBACK="1", PYTHONIOENCODING="utf-8")
        cikti = subprocess.run([sys.executable, "-c", betik], capture_output=True, text=True, encoding="utf-8",
                               env=ortam, timeout=120, check=True).stdout
        sonuc = json.loads(cikti.strip().splitlines()[-1])
        self.assertTrue(sonuc["yedek"])
        self.assertTrue(sonuc["verify"])
        self.assertEqual(sonuc["mod"], int(ssl.CERT_REQUIRED))
        self.assertTrue(sonuc["ad"])
        self.assertTrue(sonuc["unavailable"])
        self.assertTrue(sonuc["uyari"].startswith("UYARI: veri çekilemedi, teyidiniz gerekli: "))


class _Bos:
    def open(self, req, timeout=None):
        return _Yanit()


class _Yanit:
    status = 200
    headers = {}

    def read(self, n=-1):
        return b""

    def geturl(self):
        return "https://www.bddk.org.tr/x"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Erisilemez(unittest.TestCase):
    def _hata(self, *a, **kw):
        raise HttpError(0, LISTE, TLS_HATASI)

    def test_arama_bos_liste_degil_uyari_doner(self):
        with mock.patch.object(kvkk._http, "get_text", self._hata):
            out = kvkk.search({"query": "veri sorumlusu"})
        self.assertNotIn("results", out)
        self.assertTrue(out["unavailable"])
        self.assertEqual(out["error_code"], "UPSTREAM_UNAVAILABLE")
        self.assertEqual(out["uyari"], "UYARI: veri çekilemedi, teyidiniz gerekli: " + LISTE)
        self.assertIn("TLS", out["error"])

    def test_getir_uyari_ve_karar_adresi(self):
        with mock.patch.object(kvkk._http, "get_text", self._hata):
            out = kvkk.get({"id": "8887/2026-1183"})
        self.assertTrue(out["unavailable"])
        self.assertNotIn("text", out)
        self.assertEqual(out["uyari"],
                         "UYARI: veri çekilemedi, teyidiniz gerekli: https://www.kvkk.gov.tr/Icerik/8887/2026-1183")

    def test_numarayla_getir_liste_okunamazsa_uyari(self):
        with mock.patch.object(kvkk._http, "get_text", self._hata):
            out = kvkk.get({"id": "8887"})
        self.assertTrue(out["unavailable"])
        self.assertEqual(out["uyari"], "UYARI: veri çekilemedi, teyidiniz gerekli: " + LISTE)

    def test_dis_adres_uyari_baglantisi_olmaz(self):
        out = kvkk.get({"id": "http://127.0.0.1:9/x"})
        self.assertEqual(out["error_code"], "INVALID_ARGUMENT")
        self.assertNotIn("uyari", out)
        self.assertNotIn("127.0.0.1", str(out))

    def test_sunucu_araci_da_uyariyi_tasir(self):
        import server
        with mock.patch.object(kvkk._http, "get_text", self._hata):
            out = server.t_kurum_ara({"kurum": "kvkk", "query": "açık rıza"})
        self.assertEqual(out["kurum"], "kvkk")
        self.assertTrue(out["unavailable"])
        self.assertNotIn("results", out)
        self.assertTrue(out["uyari"].startswith("UYARI: veri çekilemedi, teyidiniz gerekli: https://www.kvkk.gov.tr/"))


if __name__ == "__main__":
    unittest.main()
