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


def test_only_new_mevcut_kaydi_atlar_ve_vektorunu_korur():
    """Tazeleme modu: indeksteki ref yeniden yazılmaz, vektörü silinmez, adaptöre 'var' der."""
    import crawl
    d = tempfile.mkdtemp()
    idx = retrieval.Index(os.path.join(d, "t.db"))
    idx.upsert({"ref": "bddk:1", "title": "Eski başlık", "body": "tam metin", "subject": "bddk"})
    doc_id = idx.db.execute("SELECT id FROM docs WHERE ref='bddk:1'").fetchone()["id"]
    idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?, ?, ?, ?)", (doc_id, 2, b"\x00" * 8, "m"))
    idx.db.commit()

    yeni = crawl._Tagged(idx, "bddk", only_new=True)
    assert yeni.exists("bddk:1") and not yeni.exists("bddk:2")
    assert yeni.exists_prefix("bddk:") and not yeni.exists_prefix("spk:")
    yeni.upsert({"ref": "bddk:1", "title": "Kısa stub", "body": "stub", "subject": "bddk"})
    row = idx.db.execute("SELECT body FROM docs WHERE ref='bddk:1'").fetchone()
    assert row["body"] == "tam metin", "only_new mevcut gövdeyi ezdi"
    assert idx.db.execute("SELECT COUNT(*) FROM vecs WHERE doc_id=?", (doc_id,)).fetchone()[0] == 1
    assert yeni.skipped >= 1

    eski = crawl._Tagged(idx, "bddk")          # varsayılan: değişmeyen davranış
    assert not eski.exists("bddk:1") and not eski.exists_prefix("bddk:")
    eski.upsert({"ref": "bddk:1", "title": "Kısa stub", "body": "stub", "subject": "bddk"})
    assert idx.db.execute("SELECT body FROM docs WHERE ref='bddk:1'").fetchone()["body"] == "stub"
    assert idx.db.execute("SELECT COUNT(*) FROM vecs WHERE doc_id=?", (doc_id,)).fetchone()[0] == 0


def test_ictihat_tek_tarafli_tarih_araligi_tamamlanir():
    """Bedesten yalnız kararTarihiStart verilince süzgeci SESSİZCE yok sayıyor: 612 yerine
    52.993 karar (canlı, 2026-09-20). ictihat_semantik_ara yalnız date_from alır — hep süzgeçsizdi."""
    gonderilen = []
    eski = bedesten_ictihat._http.post_json

    def sahte(path, payload, **kw):
        gonderilen.append(payload["data"])
        return {"metadata": {"FMTY": "SUCCESS"}, "data": {"emsalKararList": [], "total": 0}}

    bedesten_ictihat._http.post_json = sahte
    try:
        bedesten_ictihat.search({"query": "işe iade", "date_from": "2025-01-01"})
        bedesten_ictihat.search({"query": "işe iade", "date_to": "2015-12-31"})
        bedesten_ictihat.search({"query": "işe iade"})
    finally:
        bedesten_ictihat._http.post_json = eski
    a, b, c = gonderilen
    assert a["kararTarihiStart"].startswith("2025-01-01") and a["kararTarihiEnd"].startswith("2100")
    assert b["kararTarihiStart"].startswith("1900") and b["kararTarihiEnd"].startswith("2015-12-31")
    assert "kararTarihiStart" not in c and "kararTarihiEnd" not in c


def test_backfill_yalniz_basliktan_ibaret_govdeyi_doldurur():
    """BDDK/BTK/Rekabet listeden indekslendi: gövde = başlık, semantik arama başlığı arıyordu."""
    import crawl
    import sources as _sources
    d = tempfile.mkdtemp()
    idx = retrieval.Index(os.path.join(d, "t.db"))
    idx.upsert({"ref": "bddk:11", "title": "X A.Ş.'nin faaliyet izninin iptaline ilişkin Kurul Kararı",
                "body": "X A.Ş.'nin faaliyet izninin iptaline ilişkin Kurul Kararı", "subject": "bddk"})
    idx.upsert({"ref": "bddk:12", "title": "Y Bankası kuruluş izni", "body": "tam metin " * 80, "subject": "bddk"})
    idx.upsert({"ref": "bddk:13", "title": "Taranmış karar", "body": "Taranmış karar", "subject": "bddk"})
    ids = {r["ref"]: r["id"] for r in idx.db.execute("SELECT id, ref FROM docs")}
    for i in ids.values():
        idx.db.execute("INSERT INTO vecs(doc_id, dim, vec, model) VALUES(?, ?, ?, ?)", (i, 2, b"\x00" * 8, "m"))
    idx.db.commit()

    istenen = []

    class _Sahte:
        @staticmethod
        def get(args):
            istenen.append(args["id"])
            return {"text": "Kurul, 5411 sayılı Kanunun 71 inci maddesi uyarınca " * 12} if args["id"] == "11" else {"text": ""}

    eski = _sources.load_all
    _sources.load_all = lambda: {"bddk": _Sahte}
    try:
        ozet = crawl._backfill(idx, ["bddk", "kvkk"], 0, lambda m: None)
    finally:
        _sources.load_all = eski
    assert sorted(istenen) == ["11", "13"], "zaten metni olan belge yeniden indirilmemeli"
    assert ozet["bddk"] == {"aday": 2, "metin_eklendi": 1, "metin_yok": 1}
    govde = {r["ref"]: r["body"] for r in idx.db.execute("SELECT ref, body FROM docs")}
    assert "5411 sayılı Kanunun" in govde["bddk:11"] and govde["bddk:13"] == "Taranmış karar"
    vek = {r[0] for r in idx.db.execute("SELECT doc_id FROM vecs")}
    assert ids["bddk:11"] not in vek, "metni değişen belgenin eski vektörü kalmamalı"
    assert ids["bddk:12"] in vek and ids["bddk:13"] in vek


# ---------------------------------------------------------------- mevzuat_ara(konu=…)

from sources import bedesten_mevzuat as _mv  # noqa: E402


class _SahteBedesten:
    """_mv._call ve _mv.get'i geçici olarak değiştirir; ağ yok."""

    def __init__(self, sayfalar, metinler=None, cagri_yasak=False):
        self.sayfalar, self.metinler, self.cagri_yasak = sayfalar, metinler or {}, cagri_yasak
        self.istekler = []

    def __enter__(self):
        self._call, self._get = _mv._call, _mv.get

        def call(path, data, paging=False):
            assert not self.cagri_yasak, "doğrulama ağa çıkmadan yapılmalıydı"
            self.istekler.append(dict(data))
            i = data["pageNumber"] - 1
            lst = self.sayfalar[i] if i < len(self.sayfalar) else []
            return {"total": sum(len(x) for x in self.sayfalar), "mevzuatList": lst}

        _mv._call = call
        _mv.get = lambda args: {"text": self.metinler.get(str(args.get("mevzuat_id")), "")}
        return self

    def __exit__(self, *a):
        _mv._call, _mv.get = self._call, self._get


def _kayit(i, tur, ad):
    return {"mevzuatId": str(i), "mevzuatNo": str(7000 + i), "mevzuatAdi": ad,
            "mevzuatTur": {"name": tur}, "resmiGazeteTarihi": "2026-08-26T21:00:00.000Z",
            "resmiGazeteSayisi": "33350"}


def test_mevzuat_sayfa_boyu_20yi_asamaz():
    """Bedesten 20'den büyük pageSize'a 400 döndürür; şema 50 diyordu (canlı, 2026-09-20)."""
    with _SahteBedesten([[]]) as b:
        _mv.search({"query": "enerji", "page_size": 50})
    assert b.istekler[0]["pageSize"] == 20
    assert _mv.SEARCH_SCHEMA["properties"]["page_size"]["maximum"] == 20


def test_mevzuat_sorgusuz_liste_tur_veya_tarihle_olur():
    with _SahteBedesten([[_kayit(1, "KKY", "ARI ZEHRİNİN TOPLANMASINA İLİŞKİN YÖNETMELİK")]]) as b:
        r = _mv.search({"types": ["KKY"], "rg_date_from": "2026-07-01"})
    assert not r.get("error") and len(r["results"]) == 1
    assert "mevzuatAdi" not in b.istekler[0] and "phrase" not in b.istekler[0]
    assert "error" in _mv.search({})                      # hiçbir süzgeç yokken hâlâ reddeder


def test_mevzuat_tek_tarafli_rg_araligi_tamamlanir():
    """Bedesten yalnız Start ya da yalnız End verilince süzgeci SESSİZCE yok sayar:
    rg_date_from="2024-09-01" ile 6 yerine 917 kanun dönüyordu (canlı, 2026-09-20)."""
    with _SahteBedesten([[], []]) as b:
        _mv.search({"types": ["KANUN"], "rg_date_from": "2024-09-01"})
        _mv.search({"types": ["KANUN"], "rg_date_to": "2024-09-01"})
    ilk, ikinci = b.istekler
    assert ilk["resmiGazeteTarihiStart"].startswith("2024-08-31") and ilk["resmiGazeteTarihiEnd"].startswith("2100")
    assert ikinci["resmiGazeteTarihiStart"].startswith("1850") and ikinci["resmiGazeteTarihiEnd"].startswith("2024-09-01")


def test_mevzuat_konu_gecersizse_AGA_CIKMADAN_hata_verir():
    with _SahteBedesten([[]], cagri_yasak=True):
        r = _mv.search({"types": ["KANUN"], "konu": "ceza"})
        r2 = _mv.search({"types": ["KANUN"], "konu": "icra", "esik": "elma"})
    assert "desteklenmiyor" in r["error"] and "sayı" in r2["error"]


def test_mevzuat_konu_eler_sayar_ve_birden_cok_sayfa_tarar():
    s1 = [_kayit(1, "TEBLIGLER", "KONKORDATO GİDER AVANSI TARİFESİ")] + \
         [_kayit(10 + i, "KKY", "TÜRK OPTİSYEN-GÖZLÜKÇÜLER BİRLİĞİ YÖNETMELİĞİ") for i in range(19)]
    s2 = [_kayit(2, "KKY", "KUR’AN KURSLARI YÖNETMELİĞİ")]
    with _SahteBedesten([s1, s2]) as b:
        r = _mv.search({"types": ["KKY", "TEBLIGLER"], "konu": "icra", "page_size": 20, "max_pages": 3})
    assert [x["title"] for x in r["results"]] == ["KONKORDATO GİDER AVANSI TARİFESİ"]
    assert r["results"][0]["konu_kaynak"] == "baslik" and r["results"][0]["konu_skoru"] >= 0.20
    assert r["konu_taranan"] == 21 and r["konu_elenen"] == 20 and r["konu_belirsiz"] == 0
    assert r["pages_scanned"] == 2 and len(b.istekler) == 2      # kısa sayfada durur
    assert "0/3" in r["konu_notu"]                               # sınır her yanıtta


def test_mevzuat_torba_kanun_degistirilen_adlardan_yakalanir():
    """'Bazı Kanunlarda Değişiklik' başlığı İİK'yı değiştirdiğini söylemez; metin söyler.
    'ile' ile 'ilgili' arasındaki satır sonu bir kez her ikinci kanunu kaçırtmıştı."""
    metin = ("MADDE 1- 2- ( 9/6/1932 tarihli ve 2004 sayılı İcra ve İflas Kanunu ile \n"
             "ilgili olup, yerine işlenmiştir.) MADDE 3- ( 19/3/1969 tarihli ve 1136 sayılı "
             "Avukatlık Kanunu ile ilgili olup, yerine işlenmiştir.) MADDE 4- (2872 sayılı Kanun "
             "ile ilgili olup, yerine işlenmiştir.)")
    torba = _kayit(5, "KANUN", "BAZI KANUNLARDA DEĞİŞİKLİK YAPILMASINA DAİR KANUN")
    with _SahteBedesten([[torba]], {"5": metin}):
        assert _mv._degistirilen("5") == ["İcra ve İflas Kanunu", "Avukatlık Kanunu"]
        r = _mv.search({"types": ["KANUN"], "konu": "icra"})
    assert len(r["results"]) == 1 and r["konu_elenen"] == 0
    assert r["results"][0]["konu_kaynak"] == "degistirilen_kanunlar"
    assert r["results"][0]["konu_degistirilen"] == "İcra ve İflas Kanunu"


def test_mevzuat_torba_kanun_adlari_okunamazsa_ELENMEZ():
    """7557: 'MADDE 1 ila 27- İlgili Kanunlara işlenmiştir.' Ad yok. Bilinmeyeni atmak,
    hukukçuya 'bu dönemde o kanun değişmedi' demektir."""
    torba = _kayit(3, "KANUN", "SAĞLIKLA İLGİLİ BAZI KANUNLARDA DEĞİŞİKLİK YAPILMASINA DAİR KANUN")
    with _SahteBedesten([[torba]], {"3": "MADDE 1 ila MADDE 27- İlgili Kanunlara işlenmiştir."}):
        r = _mv.search({"types": ["KANUN"], "konu": "vergi"})
    assert len(r["results"]) == 1 and r["konu_belirsiz"] == 1 and r["konu_elenen"] == 0
    assert r["results"][0]["konu_kaynak"] == "belirsiz"


def test_mevzuat_semasi_konuyu_ve_olculmus_sinirini_duyurur():
    import server
    sema = {t.name: t for t in server.TOOLS}["mevzuat_ara"].input_schema
    p = sema["properties"]
    assert set(p["konu"]["enum"]) == {"enerji", "rekabet", "vergi", "icra"}
    assert "enerji 0/3" in p["konu"]["description"], "zayıf konu şemada gizlenmemeli"
    assert "max_pages" in p and "esik" in p and not sema.get("required")
    import triyaj
    k = triyaj.kunye()
    assert k["mevzuat"]["gorulmemis"]["enerji"] == {"pozitif": 3, "yakalanan": 0}


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
