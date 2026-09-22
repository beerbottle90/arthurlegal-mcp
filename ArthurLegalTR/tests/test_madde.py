"""SMK m.120 regresyonu: tek çağrılı madde okuma ve icinde_ara başlık ataması (ağsız).

Olay: bir protokole "SMK m.120 = ŞİRKET'in önalım hakkı" yazıldı; m.120 "Çalışanın önalım
hakkı"dır. Model maddeyi çekmemişti (iki çağrı + büyük ağaç), icinde_ara ise 120'nin
başlığını 119'un sonuna ekliyordu. Fikstür canlı Bedesten yanıtlarından kısaltıldı.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sources import bedesten_mevzuat as mv  # noqa: E402

FX = json.load(open(os.path.join(ROOT, "tests", "fixtures", "smk_m120.json"), encoding="utf-8"))


def _sahte_bedesten():
    istekler = []

    def call(path, data, paging=False):
        istekler.append((path, dict(data)))
        if path == "/searchDocuments":
            return FX["search"]
        if path == "/mevzuatMaddeTree":
            return FX["tree"]
        if path == "/getDocumentContent":
            anahtar = "%s_%s" % (data.get("documentType", "").lower(), data.get("id"))
            if anahtar in FX:
                return FX[anahtar]
        raise mv.HttpError(404, "sahte" + path, b"Record not found")

    mv._CACHE.clear()
    mv._call = call
    return istekler


def test_smk_120_tek_cagrida_dogru_baslik_ve_atif():
    istekler = _sahte_bedesten()
    r = mv.article({"number": "6769", "madde_no": "120"})
    it = r["items"][0]
    assert it["ok"] is True
    assert it["heading"] == "Çalışanın önalım hakkı"
    assert it["text"].startswith("MADDE 120-") and "iflas" in it["text"]
    assert it["citation"] == "SINAİ MÜLKİYET KANUNU (Kanun No. 6769, RG 10.01.2017/29944) m. 120"
    assert len(istekler) == 3                     # arama + ağaç + madde düğümü
    mv.article({"number": "6769", "madde_no": "120"})
    assert len(istekler) == 3                     # ikinci çağrı önbellekten


def test_madde_eş_adı_ve_liste_sirasi():
    _sahte_bedesten()
    r = mv.article({"number": "6769", "madde": ["120"]})
    assert [i["madde_no"] for i in r["items"]] == ["120"]


def test_olmayan_madde_komsu_dondurmez():
    _sahte_bedesten()
    r = mv.article({"number": "6769", "madde_no": "250"})
    it = r["items"][0]
    assert it["ok"] is False and it["error_code"] == "NOT_FOUND"
    assert not it.get("text") and not it.get("madde_id")


def test_eski_madde_id_kullanimi_anahtarlari_korur():
    _sahte_bedesten()
    r = mv.article({"madde_id": "1651731"})
    for k in ("madde_id", "text", "mime_type"):
        assert k in r
    assert "Çalışanın önalım hakkı" in r["text"]


def test_icinde_ara_basligi_kendi_maddesine_baglar():
    _sahte_bedesten()
    r = mv.search_within({"mevzuat_id": "104221", "query": "önalım"})
    sonuc = {x["madde_no"]: x for x in r["results"]}
    assert r["results"][0]["madde_no"] == "120"
    assert "Çalışanın önalım hakkı" in r["results"][0]["heading"]
    assert "119" not in sonuc                     # 119'un metninde 'önalım' yok; başlık artık onda değil


def test_talimat_madde_atfi_kurali_basta():
    import server
    assert server.INSTRUCTIONS.startswith("MADDE ATFI KURALI")
    assert "madde_no" in server.INSTRUCTIONS[:600]


if __name__ == "__main__":
    ok = 0
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
            ok += 1
            print("ok   ", t.__name__)
        except Exception as e:  # noqa: BLE001
            print("FAIL ", t.__name__, "-", repr(e))
    print("%d/%d passed" % (ok, len(tests)))
    sys.exit(0 if ok == len(tests) else 1)
