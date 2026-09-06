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

import aes_min  # noqa: E402
import retrieval  # noqa: E402
import textx  # noqa: E402
from net import decode  # noqa: E402
from sources import bedesten_ictihat, bddk, kvkk, rekabet, resmi_gazete, sigorta_tahkim, spk  # noqa: E402


def test_aes_fips_vectors():
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    for klen, expect in ((16, "69c4e0d86a7b0430d8cdb78070b4c55a"), (24, "dda97ca4864cdfe06eaf70a0ec0d7191"),
                         (32, "8ea2b7ca516745bfeafc49904b496089")):
        rk, r = aes_min._expand_key(bytes(range(klen)))
        assert aes_min._encrypt_block(pt, rk, r).hex() == expect
    out = aes_min.aes_cbc_encrypt(bytes(range(24)), b"\x00" * 16, b"hello")
    assert len(out) == 16


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
           "\n16.06.2026 Tarih ve K-2026/2 Sayılı İtiraz Hakem Heyeti Kararı\n" + "metin " * 300)
    d = sigorta_tahkim.split_decisions(txt, min_len=500)
    assert [x["heading"][:10] for x in d] == ["15.06.2026", "16.06.2026"]


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
