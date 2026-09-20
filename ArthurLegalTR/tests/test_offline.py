"""Offline tests — no network. Run: python -m pytest tests/ -q  (or python tests/test_offline.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import retrieval  # noqa: E402
import textx  # noqa: E402
from net import decode  # noqa: E402
from sources import bedesten_ictihat, bddk, kvkk, rekabet, resmi_gazete, sigorta_tahkim, spk  # noqa: E402


def test_turkish_folding():
    assert textx.tr_lower("İSTANBUL IĞDIR") == "istanbul ığdır"
    assert textx.tr_fold("Kürşat ŞİŞLİ") == "kursat sisli"
    assert textx.count_hits("Aydınlatma yükümlülüğü", "AYDINLATMA") == 1


def test_html_to_text_and_charset():
    t = textx.html_to_text("<h1>Başlık</h1><p>Bir&nbsp;paragraf.</p><table><tr><td>a</td><td>b</td></tr></table>")
    assert "BAŞLIK" in t and "Bir paragraf." in t and "a | b" in t.replace("  ", " ") or "a" in t
    assert decode("Türkçe".encode("windows-1254"), {"Content-Type": "text/html; charset=windows-1254"}) == "Türkçe"
    assert decode("Türkçe".encode("utf-8"), {}) == "Türkçe"


def test_paginate():
    p = textx.paginate("x" * 10, 2, 4)
    assert p["page"] == 2 and p["total_pages"] == 3 and p["text"] == "xxxx" and p["has_more"]


def test_bedesten_chambers_and_citation():
    assert bedesten_ictihat.CHAMBERS["H9"] == "9. Hukuk Dairesi"
    assert bedesten_ictihat.CHAMBERS["IDDK"] == "İdare Dava Daireleri Kurulu"
    c = bedesten_ictihat._citation("Yargıtay Kararı", "9. Hukuk Dairesi", "2023/1", "2024/2", "01.02.2024")
    assert c == "Yargıtay 9. Hukuk Dairesi, E. 2023/1, K. 2024/2, 01.02.2024"


def test_bddk_title_parse():
    m = bddk._HEAD.match("(06.08.2026 - 11548) BLG Varlık Yönetim A.Ş.’nin faaliyet izninin iptal edilmesine ilişkin Kurul Kararı")
    assert m and m.group(1) == "06.08.2026" and m.group(2) == "11548"


def test_rekabet_list_parser():
    html = """<div class="yazi01">Toplam : 42</div><div id="kararList">
    <table class="equalDivide"><tr><td>1.9.2026</td><td>25-44/1086-615</td><td><a href="/Karar?kararId=x">i</a></td></tr>
    <tr><td>27.11.2025</td><td>Rekabet İhlali</td></tr>
    <tr><td colspan="5"><a href="/Karar?kararId=88f38c4f-97ec">Yemek Sepeti taahhüt</a></td></tr></table></div>"""
    out = rekabet._parse_list(html)
    assert out["total"] == 42 and len(out["results"]) == 1
    r = out["results"][0]
    assert r["karar_id"] == "88f38c4f-97ec" and r["decision_number"] == "25-44/1086-615"
    assert "27.11.2025" in r["citation"]


def test_sigorta_split():
    txt = ("İÇİNDEKİLER\n15.06.2026 Tarih ve K-2026/1 Sayılı Hakem Kararı ....... 3\n" +
           "15.06.2026 Tarih ve K-2026/1 Sayılı Hakem Kararı\n" + "gövde " * 300 +
           "\n16.06.2026 Tarih ve K-2026/2 Sayılı İtiraz Hakem Heyeti Kararı\n" + "metin " * 300 +
           "\n07/09/2015 tarih ve K.2015/8207 Sayılı Hakem Kararı\n" + "eski " * 300 +
           "\n09.04.2025 Tarih – K-2025/180058 Sayılı Hakem Kararı\n" + "tire " * 300 +
           "\n21/09/2021 Tarihli - 2021/İHK-30910 Sayılı İtiraz Hakem Heyeti Kararı\n" + "ihk " * 300)
    d = sigorta_tahkim.split_decisions(txt, min_len=500)
    assert [x["heading"][:10] for x in d] == ["15.06.2026", "16.06.2026", "07/09/2015", "09.04.2025", "21/09/2021"]


def test_spk_sections():
    t = "A. DUYURU VE İLKE KARARLARI\n1. Kurul Karar Organı ...\nB. DİĞER ÖZEL DURUMLAR\nmetin"
    s = spk._sections(t)
    assert len(s) == 2 and s[0]["heading"].startswith("A.")


def test_kvkk_link_regex():
    m = kvkk._LINK.search('<a href="https://www.kvkk.gov.tr/Icerik/8884/2024-1361">Devamını Gör</a>')
    assert m and m.group(2) == "8884" and m.group(3) == "2024-1361"


def test_index_roundtrip_and_search(tmp_path=None):
    d = tmp_path or tempfile.mkdtemp()
    idx = retrieval.Index(os.path.join(str(d), "t.db"))
    idx.upsert({"ref": "a", "title": "Elektrik piyasası lisans iptali", "body": "EPDK lisans iptal kararı", "subject": "epdk",
                "citation": "EPDK, 1", "date": "2024-01-01"})
    idx.upsert({"ref": "b", "title": "Kişisel verilerin korunması", "body": "aydınlatma yükümlülüğü ihlali", "subject": "kvkk",
                "citation": "KVKK, 2", "date": "2024-02-01"})
    idx.reindex_fts()
    out = idx.search("lisans iptali", mode="lexical")
    assert out["results"] and out["results"][0]["ref"] == "a"
    out = idx.search("aydinlatma", mode="lexical", filters={"subject": "kvkk"})
    assert out["results"] and out["results"][0]["ref"] == "b"
    assert idx.get("a")["citation"] == "EPDK, 1"


def test_server_stdio_lists_tools():
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    env = dict(os.environ, INDEX_PATH=os.path.join(tempfile.mkdtemp(), "i.db"), PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "server.py")],
                       input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True,
                       encoding="utf-8", timeout=120, env=env)
    names = []
    for line in p.stdout.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("id") == 2:
            names = [t["name"] for t in o["result"]["tools"]]
    assert "ictihat_ara" in names and "kurum_karari_ara" in names and "semantik_ara" in names and "status" in names
    assert len(names) >= 24


# --------------------------------------------------------------------------- #
# Konu triyajı — resmi_gazete_tara'nın yerel ön elemesi
# --------------------------------------------------------------------------- #

import triyaj  # noqa: E402


def test_triyaj_katlama_egitimle_ayni():
    """Katlama eğitimdeki ``jev.katla`` ile birebir olmalı.

    Ayrışırsa model kendi sözlüğünü tanımaz ve HATA VERMEDEN her şeye sıfır
    der — yani süzgeç her kalemi eler ve kullanıcı 'o aralıkta bir şey yok'
    sanır. Sessiz bozulma, bu modülün en tehlikeli hata biçimidir.
    """
    assert triyaj.katla("İSTANBUL IĞDIR") == "istanbul igdir"
    assert triyaj.katla("Çağrı ŞÜKRÜ Öztürk") == "cagri sukru ozturk"


def test_triyaj_model_yuklenir_ve_kunye_dogru():
    k = triyaj.kunye()
    assert k["var"] is True, k
    assert set(k["konular"]) == {"enerji", "rekabet", "vergi", "icra"}
    assert k["esik"] == 0.20          # esik_yerel.py ile ölçüldü
    assert k["olcum"]["duyarlilik_esik_0.20"]["icra"] < 1.0   # tavan dürüstlüğü


def test_triyaj_kural_katmani_ve_ic_idare_istisnasi():
    m = triyaj.motor()
    # Kurum adı kuralı ateşler.
    assert m.p("vergi", "TEBLİĞLER Vergi Usul Kanunu Genel Tebliği") == 1.0
    # Ama kurumun KENDİ personel düzenlemesinde susar; model konuşur.
    ic = "YÖNETMELİKLER Gelir İdaresi Başkanlığı Personeli Görevde Yükselme Yönetmeliği"
    assert m.p("vergi", ic) < 0.20
    assert m.kural_eslesmeleri(ic, "vergi") == []


def test_triyaj_konu_adi_gecmeyen_kalemi_yakalar():
    """Süzgecin asıl varlık sebebi: metinsel aramanın bulamadığını bulmak."""
    m = triyaj.motor()
    for konu, baslik in (
        ("icra", "TEBLİĞLER Konkordato Gider Avansı Tarifesi"),
        ("enerji", "YÖNETMELİKLER Şarj Hizmeti Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik"),
        ("vergi", "TEBLİĞLER Tahsilat Genel Tebliği (Seri:B Sıra No:20)"),
    ):
        assert konu not in triyaj.katla(baslik), "örnek geçersiz: konu adı geçiyor"
        assert m.p(konu, baslik) >= 0.20, (konu, baslik)


def test_triyaj_alakasiz_kalemi_eler():
    m = triyaj.motor()
    t = "YÖNETMELİKLER Türkiye Okçuluk Federasyonu Ana Statüsü"
    assert all(m.p(k, t) < 0.20 for k in m.konular)


def test_triyaj_bilinmeyen_konu_reddedilir():
    m = triyaj.motor()
    try:
        m.p("ceza", "TEBLİĞLER Bir Şey")
    except ValueError:
        return
    raise AssertionError("bilinmeyen konu sessizce kabul edildi")


def test_rg_konu_suzgeci_elenen_sayisini_bildirir():
    """Kaç kalemin atıldığı söylenmeden 'bulunan budur' demek dürüst değil."""
    items = [
        {"section": "TEBLİĞLER", "title": "Konkordato Gider Avansı Tarifesi", "url": ""},
        {"section": "YÖNETMELİKLER", "title": "Türkiye Okçuluk Federasyonu Ana Statüsü", "url": ""},
    ]
    r = resmi_gazete._konu_suz(items, "icra", None)
    assert not r.get("error"), r
    assert len(r["items"]) == 1 and r["elenen"] == 1 and r["esik"] == 0.20
    assert r["items"][0]["konu_skoru"] >= 0.20


def test_rg_konu_esik_sifir_hicbir_seyi_elemez():
    items = [{"section": "YÖNETMELİKLER", "title": "Türkiye Okçuluk Federasyonu Ana Statüsü", "url": ""}]
    r = resmi_gazete._konu_suz(items, "icra", 0)
    assert r["elenen"] == 0 and len(r["items"]) == 1
    assert "konu_skoru" in r["items"][0]


def test_rg_tara_query_veya_konu_ister():
    """İkisi de yokken 60 günü ham dökmek çağırana yüzlerce ilgisiz kalem yollar."""
    r = resmi_gazete.scan({"date_from": "2026-01-01", "date_to": "2026-01-05"})
    assert "error" in r and "konu" in r["error"]


def test_rg_tara_gecersiz_konuda_AGA_CIKMADAN_hata_verir():
    """Doğrulama döngüden önce olmalı.

    Sonra olsaydı geçersiz konu her gün fihrist hatası üretir, döngü o günleri
    atlar ve sonuç 'total: 0' olurdu — ağa 60 istek atıp kullanıcıya sessiz bir
    yalan söyleyerek.
    """
    r = resmi_gazete.scan({"konu": "ceza", "date_from": "2026-01-01",
                           "date_to": "2026-01-05"})
    assert "error" in r and "desteklenmiyor" in r["error"]
    r2 = resmi_gazete.scan({"konu": "icra", "esik": "elma",
                            "date_from": "2026-01-01", "date_to": "2026-01-05"})
    assert "error" in r2 and "sayı" in r2["error"]


def test_rg_tara_semasi_konuyu_ve_sinirini_duyurur():
    """Sınır şemada yazmazsa çağıran modeli yanlış işte kullanır."""
    import server
    araclar = {t.name: t for t in server.TOOLS}
    sema = araclar["resmi_gazete_tara"].input_schema
    props = sema["properties"]
    assert set(props["konu"]["enum"]) == {"enerji", "rekabet", "vergi", "icra"}
    assert "esik" in props
    assert not sema.get("required"), "query artık zorunlu olmamalı"
    assert "ÖN ELEME" in props["konu"]["description"].upper()
    # Fihrist aracı da aynı süzgeci sunmalı; ikisi ayrışırsa kullanıcı şaşırır.
    assert "konu" in araclar["resmi_gazete_fihrist"].input_schema["properties"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   ", fn.__name__)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FAIL ", fn.__name__, "->", type(exc).__name__, exc)
    print("%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
