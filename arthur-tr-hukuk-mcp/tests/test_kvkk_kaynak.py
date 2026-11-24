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
import os
import ssl
import sys
import unittest
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
