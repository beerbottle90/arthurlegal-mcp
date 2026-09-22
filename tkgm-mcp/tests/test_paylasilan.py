"""Paylaşılan (HTTP) uç: taşıma kapısı, ortak bellek, CPU sınırları.

Canlı uç kimlik doğrulamasızdır ve bütün kullanıcılar tek süreci paylaşır; buradaki her
test, bir kullanıcının başka bir kullanıcıya ya da sunucuya ne yapabildiğini sınar.
"""

import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
import tkgm_kaynak as kaynak  # noqa: E402
import tkgm_parsel as parseller  # noqa: E402
from mcpcore import McpError  # noqa: E402
from test_offline import ornek_geojson  # noqa: E402


def _cokgen(kose, ada="1", parsel="1"):
    import math
    halka = [[29.0 + 0.001 * math.cos(2 * math.pi * i / kose), 41.0 + 0.001 * math.sin(2 * math.pi * i / kose)]
             for i in range(kose)]
    halka.append(halka[0])
    return json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature",
        "properties": {"AdaNo": ada, "ParselNo": parsel},
        "geometry": {"type": "Polygon", "coordinates": [halka]}}]})


class Ortam(unittest.TestCase):
    def setUp(self):
        self._argv = sys.argv[:]
        self._env = {k: os.environ.get(k) for k in ("MCP_TRANSPORT", "TKGM_DOSYA_ERISIMI")}
        for k in self._env:
            os.environ.pop(k, None)

    def tearDown(self):
        sys.argv[:] = self._argv
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def paylasilan(self):
        os.environ["MCP_TRANSPORT"] = "http"


class TasimaKapisi(Ortam):
    def test_kisaltilmis_bayrak_paylasilan_sayilir(self):
        # argparse `--tr http`i --transport sayar; kapı aynı ayrıştırıcıyı kullanmalı.
        for argv in (["--tr", "http"], ["--trans=http"], ["--transport", "http"], ["--port", "1", "--transport=http"]):
            sys.argv[:] = ["server.py"] + argv
            self.assertTrue(kaynak.paylasilan_mi(), argv)
            self.assertTrue(kaynak.uzak_mi(), argv)
        sys.argv[:] = ["server.py"]
        self.assertFalse(kaynak.paylasilan_mi())

    def test_ortam_degiskeni_ihtiyatla_okunur(self):
        for deger in ("http", "HTTP", " http "):
            os.environ["MCP_TRANSPORT"] = deger
            self.assertTrue(kaynak.paylasilan_mi(), deger)

    def test_dosya_bayragi_kvkk_kapisini_acmaz(self):
        self.paylasilan()
        os.environ["TKGM_DOSYA_ERISIMI"] = "1"
        self.assertFalse(kaynak.uzak_mi())
        with self.assertRaises(McpError):
            server._t_tapu({"metin": "Malik: AHMET ÖRNEK\nAda: 1\nParsel: 2\nYüzölçümü: 100"})


class OrtakBellek(Ortam):
    def test_paylasilan_ucta_ref_rastgele(self):
        self.paylasilan()
        icerik = ornek_geojson()
        r1 = json.loads(server._t_parsel_oku({"icerik": icerik}))["parseller"][0]["ref"]
        r2 = json.loads(server._t_parsel_oku({"icerik": icerik}))["parseller"][0]["ref"]
        self.assertNotEqual(r1, r2)            # aynı dosya başkasının kaydına ulaştırmaz
        self.assertEqual(len(r1), 16)
        yerel = parseller.oku(icerik)[0]["ref"]
        self.assertNotIn(yerel, (r1, r2))      # içerikten hesaplanan ref paylaşılan uçta işe yaramaz

    def test_yerel_kipte_ref_icerikten(self):
        icerik = ornek_geojson()
        r1 = json.loads(server._t_parsel_oku({"icerik": icerik}))["parseller"][0]["ref"]
        self.assertEqual(r1, parseller.oku(icerik)[0]["ref"])

    def test_status_paylasilan_ucta_sayi_vermez(self):
        self.paylasilan()
        self.assertIsNone(server._t_status({})["bellekteki_parsel"])

    def test_parsel_basina_kose_siniri(self):
        with self.assertRaises(McpError):
            server._t_parsel_oku({"icerik": _cokgen(parseller.AZAMI_KOSE + 10)})
        self.assertEqual(json.loads(server._t_parsel_oku({"icerik": _cokgen(500)}))["okunan"], 1)

    def test_kose_butcesi_eskileri_dusurur(self):
        eski = parseller._KOSE_BUTCESI
        parseller._KOSE_BUTCESI = 2500
        try:
            refs = [json.loads(server._t_parsel_oku({"icerik": _cokgen(1000, parsel=str(i))}))["parseller"][0]["ref"]
                    for i in range(4)]
            self.assertLessEqual(sum(parseller._KOSE.values()), 2500)
            self.assertIsNone(parseller.getir(refs[0]))
            self.assertIsNotNone(parseller.getir(refs[-1]))
        finally:
            parseller._KOSE_BUTCESI = eski

    def test_paylasilan_kayit_omru(self):
        self.paylasilan()
        eski = parseller._OMUR_SN
        parseller._OMUR_SN = -1.0
        try:
            ref = json.loads(server._t_parsel_oku({"icerik": ornek_geojson()}))["parseller"][0]["ref"]
            self.assertIsNone(parseller.getir(ref))
        finally:
            parseller._OMUR_SN = eski

    def test_eszamanli_erisim_bozulmaz(self):
        p = parseller.oku(ornek_geojson())[0]
        hatalar = []

        def is_():
            try:
                for i in range(300):
                    kopya = dict(p, ref="es-%d-%d" % (threading.get_ident(), i % 40))
                    parseller.sakla(kopya)
                    parseller.getir(kopya["ref"])
                    parseller.bellektekiler()
            except Exception as exc:  # noqa: BLE001
                hatalar.append(exc)

        isler = [threading.Thread(target=is_) for _ in range(8)]
        for t in isler:
            t.start()
        for t in isler:
            t.join()
        self.assertEqual(hatalar, [])
        self.assertEqual(len(parseller._KOSE), len(parseller._BELLEK))


class CpuSinirlari(Ortam):
    def test_tekrarlanan_komsular_tekillesir(self):
        ref = json.loads(server._t_parsel_oku({"icerik": ornek_geojson()}))["parseller"][0]["ref"]
        p = parseller.getir(ref)
        self.assertEqual(server._komsular({"komsular": [ref] * 16}, p), [])

    def test_paylasilan_ucta_ref_sayisi_sinirli(self):
        self.paylasilan()
        ref = json.loads(server._t_parsel_oku({"icerik": ornek_geojson()}))["parseller"][0]["ref"]
        with self.assertRaises(McpError):
            server._t_kroki({"ref": ref, "komsular": ["x%d" % i for i in range(65)]})

    def test_paylasilan_ucta_cagri_basina_parsel_siniri(self):
        self.paylasilan()
        ozellikler = [json.loads(_cokgen(4, parsel=str(i)))["features"][0] for i in range(51)]
        with self.assertRaises(McpError):
            server._t_parsel_oku({"icerik": json.dumps({"type": "FeatureCollection", "features": ozellikler})})


if __name__ == "__main__":
    unittest.main()
