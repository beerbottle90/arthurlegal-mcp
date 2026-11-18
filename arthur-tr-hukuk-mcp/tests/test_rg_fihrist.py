"""Resmî Gazete fihristi: kanun kalemleri — ağsız, unittest.

    python -m unittest discover -s tests -p test_rg_fihrist.py -v      # arthur-tr-hukuk-mcp'den

Yasama Bölümü'nde kanunlar "––" ile değil kanun numarasıyla başlar ("7554 Bazı Kanunlarda …"). Ayrıştırıcı
yalnız "––" satırlarını okuyunca kanunlar fihristte de taramada da görünmüyordu (canlı, 19.11.2026: RG
12.03.2024, S. 32487 fihristi 24 kalem veriyor, "KANUN" başlığı altındaki 7499 sayılı Kanun yok).
Örnek sayfa: RG 24.07.2025, S. 32965 fihristi, sitedeki gibi (windows-1254).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sources import resmi_gazete as rg  # noqa: E402

B = "https://www.resmigazete.gov.tr/eskiler/"
with open(os.path.join(HERE, "fixtures", "rg_20250724.htm"), "rb") as _f:
    SAYFA = _f.read().decode("cp1254")


def _duz(s: str) -> str:
    return " ".join(s.split())


class KanunKalemleri(unittest.TestCase):
    def fihrist(self, sayfa=SAYFA, **args):
        istenen = []

        def getir(url, **_kw):
            istenen.append(url)
            return sayfa

        with mock.patch.object(rg._http, "get_text", side_effect=getir):
            r = rg.fihrist(dict({"date": "2025-07-24"}, **args))
        self.assertEqual(istenen, [B + "2025/07/20250724.htm"])
        return r

    def test_kanunlar_listede(self):
        r = self.fihrist()
        self.assertEqual(r["citation"], "RG 24.07.2025, S. 32965")
        kanunlar = [i for i in r["items"] if i["section"] == "KANUNLAR"]
        self.assertEqual([i["kanun_no"] for i in kanunlar], ["7554", "7555", "7557"])
        self.assertEqual(_duz(kanunlar[0]["title"]), "7554 Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun")
        self.assertEqual([i["url"] for i in kanunlar],
                         [B + "2025/07/20250724-%d.htm" % n for n in (1, 2, 3)])
        # "––" kalemleri değişmedi: 15 kalem, kanunlardan sonra, kanun_no taşımaz
        self.assertEqual(r["total"], 18)
        self.assertEqual(r["items"][3]["section"], "MİLLETLERARASI ANDLAŞMA")
        self.assertTrue(all("kanun_no" not in i for i in r["items"][3:]))

    def test_tekil_kanun_basligi(self):
        """Tek kanunlu günde başlık "KANUN"dur (RG 12.03.2024: "KANUN / 7499 Ceza Muhakemesi Kanunu ile …")."""
        r = self.fihrist(SAYFA.replace(">KANUNLAR<", ">KANUN<"))
        self.assertEqual([i["kanun_no"] for i in r["items"] if i["section"] == "KANUN"], ["7554", "7555", "7557"])

    def test_sorgu_kanun_numarasini_bulur(self):
        r = self.fihrist(query="7555")
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["items"][0]["url"], B + "2025/07/20250724-2.htm")

    def test_kanun_disi_bolumde_numarali_satir_kalem_degil(self):
        sayfa = SAYFA.replace("</body>", '<p class="MsoNormal"><a href="20250724-99.htm">2025 Yılı Yatırım '
                                         'Programı</a></p></body>')
        self.assertEqual(self.fihrist(sayfa)["total"], 18)

    def test_tarama_kanunu_bulur(self):
        with mock.patch.object(rg._http, "get_text", return_value=SAYFA):
            r = rg.scan({"query": "Kanunlarda Değişiklik", "date_from": "2025-07-24", "date_to": "2025-07-24"})
        self.assertEqual(r["days_scanned"], 1)
        bulunan = [h for h in r["results"] if h.get("kanun_no")]
        self.assertEqual([h["kanun_no"] for h in bulunan], ["7554", "7555", "7557"])   # sorgu kelime kelime
        self.assertEqual(bulunan[0]["citation"], "RG 24.07.2025, S. 32965")


if __name__ == "__main__":
    unittest.main()
