"""ArthurTapu arayüzünün üç dili (TR/AZ/EN): sözlükte eksik çeviri varsa test düşer.

Anahtar Türkçe metindir (ui/dil-az.json, ui/dil-en.json). Toplanan metinler:
  - ui/index.html: cev("…") / cevDugum("…") çağrıları, data-cev öğelerinin metni, data-cev-ph / data-cev-title /
    data-cev-aria değerleri, sekme adları (data-ad), tapu kaydı alan adları (TAPU_ALANLARI);
  - sunucunun arayüzde görünen sabit metinleri: tkgm_ui'deki _m("…"), 3D uyarısı (tkgm_harita.UYARI_3D), onay
    kartının kalıbı (tkgm_canli.ONAY_KARTI_KALIBI), harç işlem menüsü, hukuk konu başlıkları, nitelik uyarıları.
Araçların Claude'a giden çıktıları Türkçe kalır; bu test onlara bakmaz.
"""

import ast
import json
import os
import re
import sys
import unittest
from html.parser import HTMLParser

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KOK)

import tkgm_canli as canli  # noqa: E402
import tkgm_harc as harc  # noqa: E402
import tkgm_harita as harita  # noqa: E402
import tkgm_hukuk as hukuk  # noqa: E402

UI = os.path.join(KOK, "ui")
MARKALAR = ("ArthurTapu", "ArthurLegal", "ArthurMask", "TKGM", "UYAP")


class _Toplayici(HTMLParser):
    """data-cev'li öğelerin metni ve data-cev-* / data-ad öznitelikleri."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metinler, self._yigin = set(), []

    def handle_starttag(self, etiket, oznitelik):
        o = dict(oznitelik)
        for ad in ("data-cev-ph", "data-cev-title", "data-cev-aria", "data-ad"):
            if o.get(ad):
                self.metinler.add(o[ad])
        self._yigin.append([etiket, "data-cev" in o, []])

    def handle_endtag(self, etiket):
        while self._yigin:
            e, isaretli, parca = self._yigin.pop()
            if isaretli:
                metin = "".join(parca)
                if metin.strip():
                    self.metinler.add(metin)
            if e == etiket:
                break

    def handle_data(self, veri):
        for kayit in self._yigin:
            kayit[2].append(veri)


def sayfa_metinleri():
    with open(os.path.join(UI, "index.html"), encoding="utf-8") as fh:
        sayfa = fh.read()
    betik = sayfa[sayfa.index('<script>\n"use strict";'):]
    metinler = set()
    for m in re.finditer(r'\bcev(?:Dugum)?\(\s*"((?:[^"\\]|\\.)*)"', betik):
        metinler.add(json.loads('"' + m.group(1) + '"'))
    alanlar = re.search(r"const TAPU_ALANLARI = \{(.*?)\};", betik, re.S).group(1)
    metinler.update(re.findall(r':\s*"([^"]+)"', alanlar))
    t = _Toplayici()
    # Betikler ayrıştırıcıyı şaşırtmasın: yalnız gövdenin HTML'i.
    t.feed(re.sub(r"<script\b.*?</script>", "", sayfa, flags=re.S))
    metinler.update(t.metinler)
    return metinler


def sunucu_metinleri():
    metinler = {harita.UYARI_3D, canli.ONAY_KARTI_KALIBI}
    with open(os.path.join(KOK, "tkgm_ui.py"), encoding="utf-8") as fh:
        agac = ast.parse(fh.read())
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Call) and getattr(dugum.func, "id", None) == "_m" and dugum.args \
                and isinstance(dugum.args[0], ast.Constant) and isinstance(dugum.args[0].value, str):
            metinler.add(dugum.args[0].value)
    menu = harc.islemler()
    for i in menu["islemler"]:
        metinler.add(i["ad"])
        if i.get("tutar"):
            metinler.add(i["tutar"])
    metinler.update(x for x in (menu.get("uyari"), menu.get("yk_kaynagi")) if x)
    metinler.update(k["baslik"] for k in hukuk.KONULAR.values())
    metinler.update(u for _, u, _ in hukuk.NITELIK_REJIMLERI)
    metinler.update(("il", "ilçe", "mahalle"))          # canli.Belirsiz.tur: "birden çok {1} ile eşleşti"
    return metinler


def sozluk(dil):
    with open(os.path.join(UI, "dil-%s.json" % dil), encoding="utf-8") as fh:
        return json.load(fh)


class Sozlukler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.anahtarlar = sayfa_metinleri() | sunucu_metinleri()
        cls.sozlukler = {d: sozluk(d) for d in ("az", "en")}

    def test_metin_toplandi(self):
        self.assertGreater(len(self.anahtarlar), 150)
        for ornek in ("Güncellemeleri denetle", "Yüksek karşıtlık", "TKGM'den getir", "Sorgula", "Özet",
                      harita.UYARI_3D, canli.ONAY_KARTI_KALIBI):
            self.assertIn(ornek, self.anahtarlar)

    def test_eksik_ceviri_yok(self):
        for dil, s in self.sozlukler.items():
            eksik = sorted(k for k in self.anahtarlar if not str(s.get(k) or "").strip())
            self.assertEqual(eksik, [], "%s sözlüğünde %d eksik çeviri" % (dil, len(eksik)))

    def test_kullanilmayan_anahtar_yok(self):
        for dil, s in self.sozlukler.items():
            fazla = sorted(set(s) - self.anahtarlar)
            self.assertEqual(fazla, [], "%s sözlüğünde kullanılmayan anahtar" % dil)

    def test_yer_tutucular_ve_markalar_korunur(self):
        for dil, s in self.sozlukler.items():
            for k, v in s.items():
                self.assertEqual(sorted(re.findall(r"\{\d+\}", k)), sorted(re.findall(r"\{\d+\}", v)), (dil, k))
                for marka in MARKALAR:
                    if marka in k:
                        self.assertIn(marka, v, (dil, marka, k))

    def test_3d_uyarisi_anlami_korur(self):
        # Çeviri uyarının bütün öğelerini taşır: tahmin olduğu, gerçek veriye dayanmadığı, resmî işlemde kullanılamayacağı.
        en = self.sozlukler["en"][harita.UYARI_3D]
        for parca in ("ESTIMATE", "not based on real", "official", "artificial intelligence"):
            self.assertIn(parca, en)
        az = self.sozlukler["az"][harita.UYARI_3D]
        for parca in ("TƏXMİN", "rəsmi", "Süni intellekt"):
            self.assertIn(parca, az)

    def test_azerbaycanca_latin_alfabesi(self):
        kiril = re.compile(r"[Ѐ-ӿ]")
        self.assertFalse([k for k, v in self.sozlukler["az"].items() if kiril.search(v)])


if __name__ == "__main__":
    unittest.main()
