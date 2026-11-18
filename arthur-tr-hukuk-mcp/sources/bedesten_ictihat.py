"""Yargıtay, Danıştay, yerel hukuk, istinaf ve KYB kararları — Bedesten (Adalet Bakanlığı).

Upstream: ``https://bedesten.adalet.gov.tr/emsal-karar`` — the JSON backend of
``mevzuat.adalet.gov.tr/ictihat``. Two endpoints:

    POST /searchDocuments      {"data": {...}, "applicationName": "UyapMevzuat", "paging": true}
    POST /getDocumentContent   {"data": {"documentId": ...}, "applicationName": "UyapMevzuat"}

The document comes back base64-encoded as HTML or PDF. Measured rate limit
(2026-05, per source IP): 10 requests / 30 s. One request every 3.5 s keeps
clear of it; a 429 pauses the bucket for the server's Retry-After.

Search syntax accepted by ``phrase``: bare words (OR -- measured, see
``_ladder``), ``"tam cümle"``, ``+zorunlu``, ``-hariç``, ``AND``/``OR``/``NOT``.
No wildcards. Only letters, digits and a few query signs: ``2014/27629`` and
``01.04.1974`` are refused, ``2014 27629`` and ``01 04 1974`` are searched (see
``_temizle``).

Ordering: Bedesten sorts by date only when a sort field is sent. Without one
it answers in its own relevance order (see ``_SIRALAMA``), which is the default
here.

Citation contract: cite as ``Yargıtay 9. HD, E. 2023/1234, K. 2024/567,
12.03.2024`` using the *esasNo/kararNo/kararTarihiStr* fields verbatim; the
``source_url`` is ``https://mevzuat.adalet.gov.tr/ictihat/<documentId>``.

Ported from saidsurucu/yargi-mcp (MIT) — request shape, chamber map and the
rate-limit figures are his; the transport is rewritten on the standard library.
"""

from __future__ import annotations

import base64
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from net import Http, HttpError, TokenBucket
from textx import html_to_text, paginate, pdf_to_text, HAS_PYPDF
from sources import Source

BASE = "https://bedesten.adalet.gov.tr"
HEADERS = {
    "Accept": "*/*",
    "AdaletApplicationName": "UyapMevzuat",
    "Origin": "https://mevzuat.adalet.gov.tr",
    "Referer": "https://mevzuat.adalet.gov.tr/",
}

# One shared bucket for BOTH Bedesten services (ictihat + mevzuat): the limit
# is per source IP, not per path.
BUCKET = TokenBucket(
    capacity=int(os.environ.get("BEDESTEN_RATE_CAPACITY", "1")),
    refill_s=float(os.environ.get("BEDESTEN_RATE_REFILL_S", "3.5")),
    max_wait=float(os.environ.get("BEDESTEN_RATE_MAX_WAIT_S", "20")),
)
_http = Http(BASE, HEADERS, bucket=BUCKET)

COURT_TYPES = {
    "YARGITAYKARARI": "Yargıtay",
    "DANISTAYKARAR": "Danıştay",
    "YERELHUKUK": "Yerel Hukuk Mahkemeleri",
    "ISTINAFHUKUK": "Bölge Adliye Mahkemesi (istinaf, hukuk)",
    "KYB": "Kanun Yararına Bozma",
}

# Abbreviated chamber codes → the exact strings the API expects.
CHAMBERS: Dict[str, str] = {}
for _i in range(1, 24):
    CHAMBERS["H%d" % _i] = "%d. Hukuk Dairesi" % _i
    CHAMBERS["C%d" % _i] = "%d. Ceza Dairesi" % _i
for _i in range(1, 18):
    CHAMBERS["D%d" % _i] = "%d. Daire" % _i
CHAMBERS.update({
    "HGK": "Hukuk Genel Kurulu", "CGK": "Ceza Genel Kurulu", "BGK": "Büyük Genel Kurulu",
    "HBK": "Hukuk Daireleri Başkanlar Kurulu", "CBK": "Ceza Daireleri Başkanlar Kurulu",
    "DBGK": "Büyük Gen.Kur.", "IDDK": "İdare Dava Daireleri Kurulu",
    "VDDK": "Vergi Dava Daireleri Kurulu", "IBK": "İçtihatları Birleştirme Kurulu",
    "IIK": "İdari İşler Kurulu", "DBK": "Başkanlar Kurulu",
    "AYIM": "Askeri Yüksek İdare Mahkemesi",
    "AYIMDK": "Askeri Yüksek İdare Mahkemesi Daireler Kurulu",
    "AYIMB": "Askeri Yüksek İdare Mahkemesi Başsavcılığı",
    "AYIM1": "Askeri Yüksek İdare Mahkemesi 1. Daire",
    "AYIM2": "Askeri Yüksek İdare Mahkemesi 2. Daire",
    "AYIM3": "Askeri Yüksek İdare Mahkemesi 3. Daire",
})


def _iso(d: Optional[str], end: bool = False) -> Optional[str]:
    if not d:
        return None
    d = d.strip()
    if "T" in d:
        return d
    return d + ("T23:59:59.999Z" if end else "T00:00:00.000Z")


# A query the user already shaped (a quote, a +/- prefixed word, a boolean
# operator) is sent as written; only plain multi-word queries get the ladder.
_OPERATOR = re.compile(r'"|(?:^|\s)[+-]\S|\b(?:AND|OR|NOT)\b')

# Ordering. Until 0.8.0 every search was sent with sortFields=KARAR_TARIHI, so a
# question got the newest decisions that happened to share its words and the
# decision that settles it sat hundreds of rows down. Measured 2026-11-18,
# Yargıtay + Danıştay, six plain-word queries on one enforcement-law question
# (totals 13 to 1,230): sorted by date, a controlling decision was in the first
# ten for 0 of the 6; with no sort field, for 5 of the 6 and first in 4. The
# shortlist reranker (ictihat_semantik_ara) reads this same first page, so it
# was ranking the newest five, not the most relevant five. Newest-first stays
# one argument away: it is the right order for "what did the chamber say last".
# "desc" and "asc" are the pre-0.8.0 spellings of yeni and eski.
_SIRALAMA = {"ilgi": None, "yeni": "desc", "eski": "asc", "desc": "desc", "asc": "asc"}
_SIRALAMA_ADI = {None: "ilgi", "desc": "yeni", "asc": "eski"}

# Bedesten refuses every query that holds a sign outside a short list ("Sadece harf
# ve rakam içeren aramalar yapılabilir"), so a docket number, a date or an İBK
# number typed the way it is cited fails. Measured 2026-11-19 (Yargıtay +
# Danıştay), one request per sign: accepted are the letters of the Turkish and
# English alphabets, digits, spaces and the query signs " + - ( ) ! & |; refused
# are . / , ' ’ : ; ? * % § _ – “ ” and letters such as â and é. The index splits
# on those signs, so a space in their place finds the same text: the phrase
# "01 04 1974" -> 193 decisions, "+muris +muvazaası +01 +04 +1974" -> 169,
# "+muris +muvazaası +01041974" -> 0.
_HARFLER = frozenset("abcçdefgğhıijklmnoöprsştuüvyzqwx" "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZQWX")
_KABUL = _HARFLER | frozenset('0123456789"+-()!&|')
_TIRNAK = {"“": '"', "”": '"', "„": '"'}     # Word's quotes: the writer meant a phrase
_TERIM = re.compile(r'([+-]?)("[^"]*"|\S+)')
_OLDUGU_GIBI = "olduğu gibi"
_LADDER_LABELS = {"ifade": "tam ifade", "hepsi": "kelimelerin hepsi", "herhangi": "kelimelerden herhangi biri"}


def _temizle(phrase: str) -> Tuple[str, List[Tuple[str, str]]]:
    """``phrase`` with every sign Bedesten refuses replaced, and what was replaced.

    A refused sign becomes a space; a letter outside the alphabet loses its
    accent (``zekâ`` -> ``zeka``); Word's curly quotes become straight ones. The
    second value lists (sign, replacement) pairs in order of first appearance; it
    is empty, and ``phrase`` is returned untouched, when nothing was refused.
    """
    out: List[str] = []
    degisen: Dict[str, str] = {}
    for ch in phrase:
        if ch in _KABUL or ch.isspace():
            out.append(ch)
            continue
        if ch in _TIRNAK:
            yeni = _TIRNAK[ch]
        elif unicodedata.combining(ch):
            yeni = ""                      # a loose accent mark, e.g. the dot "İ".lower() leaves behind
        else:
            taban = "".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c))
            yeni = taban if taban and all(c in _HARFLER for c in taban) else " "
        out.append(yeni)
        degisen.setdefault(ch, yeni)
    if not degisen:
        return phrase, []
    return " ".join("".join(out).split()), list(degisen.items())


def _ladder(phrase: str, mode: str) -> List[Tuple[str, str]]:
    """Queries to try for ``phrase``, most precise first, as (query, label) pairs.

    Bedesten joins bare words with OR. Measured 2026-10-09 on Yargıtay +
    Danıştay: ``kira tespit davası`` unquoted -> 2,514,700 hits, newest first, the
    top one a pasture case; ``+kira +tespit +davası`` -> 53,830, a criminal
    chamber among the first two; ``"kira tespit davası"`` -> 2,127, the 3rd civil
    chamber's rent decisions. Sorted by date, the unquoted query is noise, so a
    plain multi-word query is tried as a phrase first and widened only when the
    stricter rung returns nothing -- the same precision-first ladder the local
    index uses.

    ``hepsi`` is a request, not a starting point: it is the every-word rung alone.
    Until 0.8.1 it fell through to the bare query when no decision held every
    word, and "decisions with all four words" came back as 146,718 decisions with
    any of them (measured 2026-11-19: ``yapay zeka performans fesih``, Yargıtay +
    Danıştay + istinaf). Zero is the honest answer there; the default ``ifade``
    ladder still widens, and says so in the response.
    """
    words = phrase.split()
    if mode == "herhangi" or len(words) < 2 or _OPERATOR.search(phrase):
        return [(phrase, _OLDUGU_GIBI)]
    hepsi = (" ".join("+" + w for w in words), _LADDER_LABELS["hepsi"])
    if mode == "hepsi":
        return [hepsi]
    return [('"%s"' % phrase, _LADDER_LABELS["ifade"]), hepsi, (phrase, _LADDER_LABELS["herhangi"])]


def _isaret_notu(degisen: List[Tuple[str, str]], phrase: str) -> str:
    parcalar = ['"%s" -> %s' % (eski, "boşluk" if yeni == " " else '"%s"' % yeni if yeni else "silindi")
                for eski, yeni in degisen]
    return ("Bedesten yalnız harf, rakam ve arama işaretlerini (\" + - ( )) kabul eder; dizinde bu işaretler kelime "
            "ayırıcıdır, boşluklu yazım aynı metni bulur. Çevrilenler: %s. Çevrilen sorgu: %s "
            "(Bedesten'e giden biçim uygulanan_sorgu'da)." % (", ".join(parcalar), phrase))


def _veya_onerisi(phrase: str) -> str:
    """The every-term-required form of a query that mixes a quoted phrase with unsigned terms, or "".

    Measured 2026-11-18 (Yargıtay): ``"takas mahsup" "dosya alacağı" haciz`` -> 1,290,298
    hits, because unsigned terms beside a quote are still joined with OR;
    ``+"takas mahsup" +"dosya alacağı" +haciz`` -> 395. A query with an explicit
    AND/OR/NOT is left alone: its author chose the joins.
    """
    if '"' not in phrase:
        return ""
    terimler = _TERIM.findall(phrase)
    if len(terimler) < 2 or any(t in ("AND", "OR", "NOT") for _, t in terimler):
        return ""
    if all(isaret for isaret, _ in terimler):
        return ""
    return " ".join((isaret or "+") + t for isaret, t in terimler)


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    phrase = (args.get("query") or "").strip()
    if not phrase:
        return {"error": "query gerekli. Örnek: 'lisans iptali \"hizmet kusuru\" -tazminat'"}
    phrase, degisen = _temizle(phrase)
    if not phrase:
        return {"error": "query yalnız Bedesten'in kabul etmediği işaretlerden oluşuyor (%s). Kelime ya da "
                         "rakam yazın." % " ".join(eski for eski, _ in degisen)}
    sira = (args.get("sort") or "ilgi").strip().lower()
    if sira not in _SIRALAMA:
        return {"error": "sort: ilgi, yeni ya da eski."}
    yon = _SIRALAMA[sira]
    mode = (args.get("kelime_modu") or "ifade").strip().lower()
    if mode not in _LADDER_LABELS:
        return {"error": "kelime_modu: ifade, hepsi ya da herhangi."}
    courts = args.get("courts") or ["YARGITAYKARARI", "DANISTAYKARAR"]
    if isinstance(courts, str):
        courts = [c.strip() for c in courts.split(",") if c.strip()]
    bad = [c for c in courts if c not in COURT_TYPES]
    if bad:
        return {"error": "Bilinmeyen mahkeme türü: %s. Geçerli: %s" % (bad, list(COURT_TYPES))}
    page_size = max(1, min(int(args.get("page_size") or 10), 10))
    data: Dict[str, Any] = {
        "pageSize": page_size,
        "pageNumber": max(1, int(args.get("page") or 1)),
        "itemTypeList": courts,
        "phrase": phrase,
    }
    if yon:
        data["sortFields"] = ["KARAR_TARIHI"]
        data["sortDirection"] = yon
    chamber = (args.get("chamber") or "").strip()
    if chamber and chamber.upper() != "ALL":
        data["birimAdi"] = CHAMBERS.get(chamber.upper(), chamber)
    # Bedesten TEK TARAFLI tarih aralığını SESSİZCE yok sayar (canlı, 2026-09-20): "işe iade" için
    # yalnız date_from=2025-01-01 -> 52.993 karar (süzgeçsiz), iki uç birlikte -> 612. Eksik ucu biz
    # doldururuz; yoksa "2025'ten sonraki kararlar" diyen hukukçu 1990'ların kararını okur.
    bas = _iso(args["date_from"]) if args.get("date_from") else ""
    son = _iso(args["date_to"], end=True) if args.get("date_to") else ""
    if bas or son:
        data["kararTarihiStart"] = bas or "1900-01-01T00:00:00.000Z"
        data["kararTarihiEnd"] = son or "2100-01-01T23:59:59.999Z"
    steps = _ladder(phrase, mode)
    tried: List[Dict[str, Any]] = []
    for query, label in steps:
        data["phrase"] = query
        payload = {"data": data, "applicationName": "UyapMevzuat", "paging": True}
        try:
            body = _http.post_json("/emsal-karar/searchDocuments", payload)
        except HttpError as exc:
            return _http_error(exc)
        meta = body.get("metadata") or {}
        if meta.get("FMTY") != "SUCCESS":
            return {"error": "Bedesten: %s" % (meta.get("FMTE") or meta), "query": phrase}
        d = body.get("data") or {}
        tried.append({"sorgu": query, "mod": label, "total": d.get("total", 0)})
        if d.get("total", 0):
            break
    items = []
    for e in d.get("emsalKararList") or []:
        it = e.get("itemType") or {}
        items.append({
            "document_id": e.get("documentId"),
            "court_type": it.get("name"),
            "court": it.get("description"),
            "chamber": e.get("birimAdi"),
            "esas_no": e.get("esasNo"),
            "karar_no": e.get("kararNo"),
            "karar_tarihi": e.get("kararTarihiStr"),
            "karar_turu": e.get("kararTuru"),
            "kesinlesme": e.get("kesinlesmeDurumu"),
            "citation": _citation(it.get("description"), e.get("birimAdi"), e.get("esasNo"),
                                  e.get("kararNo"), e.get("kararTarihiStr")),
            "source_url": "https://mevzuat.adalet.gov.tr/ictihat/%s" % e.get("documentId"),
        })
    out = {
        "query": phrase, "courts": courts, "page": data["pageNumber"], "page_size": page_size,
        "total": d.get("total", 0), "siralama": _SIRALAMA_ADI[yon], "results": items,
        "note": "Metin için ictihat_getir(document_id). Karar listesi metin içermez; "
                "alıntı için citation alanını birebir kullanın.",
    }
    if out["total"] > len(items):       # tek sayfaya sığan sonuçta sıra bir şey gizlemez
        out["siralama_notu"] = (
            "İlgiye göre sıralı; eski tarihli karar öne çıkabilir. Güncel uygulama için aynı sorguyu "
            "sort='yeni' ile de çalıştırın." if not yon else
            "Tarihe göre sıralı: konuyu çözen karar ilk sayfada olmayabilir. Konu araması için sort='ilgi'.")
    if degisen:
        out["sorgu_notu"] = _isaret_notu(degisen, phrase)
    oneri = _veya_onerisi(phrase)
    if oneri:
        out["onerilen_sorgu"] = oneri
        out["arama_notu"] = ("Tırnaklı ifadenin yanındaki işaretsiz terimleri Bedesten VEYA ile birleştirir; "
                             "toplam bu yüzden büyük olabilir. Hepsi geçsin istiyorsanız onerilen_sorgu'yu kullanın.")
    merdiven = steps[0][1] != _OLDUGU_GIBI
    if merdiven or degisen:
        out["uygulanan_sorgu"] = data["phrase"]
    if merdiven:
        out["arama_modu"] = tried[-1]["mod"]
        out["denenenler"] = tried
    if merdiven and mode == "hepsi":
        out["arama_notu"] = (
            "Yalnız kelimelerin hepsini birlikte içeren kararlar arandı (kelime_modu='hepsi'); sonuç yoksa "
            "VEYA'ya genişletilmez.")
        if not out["total"]:
            out["sonuc_notu"] = (
                "Seçilen mahkemelerde bu kelimelerin hepsini birlikte içeren karar yok. Bu, konuda karar "
                "olmadığını göstermez: kelime sayısını azaltın, eş anlamlısını ya da başka çekimini deneyin "
                "(fesih/feshi, zekâ yerine zeka); kelimelerden herhangi birini içeren geniş tarama için "
                "kelime_modu='herhangi'.")
    elif merdiven:
        out["arama_notu"] = (
            "Tırnaksız çok kelimeli sorgu en dar biçimden başlanarak arandı. Bedesten "
            "çıplak kelimeleri VEYA ile birleştirir; tırnaksız arama milyonlarca ilgisiz "
            "karar getirir. Değiştirmek için kelime_modu: 'hepsi' (her kelime geçsin, yoksa 0) ya da "
            "'herhangi' (Bedesten'in ham davranışı).")
        if tried[-1]["mod"] == _LADDER_LABELS["herhangi"] and out["total"]:
            out["genisleme_notu"] = (
                "Hiçbir kararda bu kelimelerin hepsi birlikte geçmiyor; bu sonuçlar kelimelerden HERHANGİ "
                "BİRİNİ içeren kararlardır ve toplam, konuyla ilgili karar sayısı değildir. Her kelime şart "
                "olsun diye kelime_modu='hepsi' (sonuç yoksa 0 döner).")
    return out


_SHORT = {"Yargıtay Kararı": "Yargıtay", "Danıştay Kararı": "Danıştay",
          "Yerel Hukuk Mahkemesi Kararı": "", "İstinaf Hukuk Mahkemesi Kararı": "",
          "Kanun Yararına Bozma Kararı": "Yargıtay (KYB)"}


def _citation(court: Optional[str], chamber: Optional[str], esas: Optional[str],
              karar: Optional[str], tarih: Optional[str]) -> str:
    court = _SHORT.get(court or "", court)
    parts = [p for p in (court, chamber) if p]
    s = " ".join(parts)
    if esas:
        s += ", E. %s" % esas
    if karar:
        s += ", K. %s" % karar
    if tarih:
        s += ", %s" % tarih
    return s.strip(", ")


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    doc_id = str(args.get("document_id") or "").strip()
    if not doc_id:
        return {"error": "document_id gerekli (ictihat_ara sonucundaki document_id)."}
    payload = {"data": {"documentId": doc_id}, "applicationName": "UyapMevzuat"}
    try:
        body = _http.post_json("/emsal-karar/getDocumentContent", payload)
    except HttpError as exc:
        return _http_error(exc)
    meta = body.get("metadata") or {}
    if meta.get("FMTY") != "SUCCESS" or not body.get("data"):
        return {"error": "Bedesten: %s" % (meta.get("FMTE") or meta), "document_id": doc_id}
    d = body["data"]
    raw = base64.b64decode(d.get("content") or b"")
    mime = d.get("mimeType") or ""
    if "html" in mime:
        text = html_to_text(raw.decode("utf-8", "replace"))
    elif "pdf" in mime:
        text, _ = pdf_to_text(raw)
        if not text and not HAS_PYPDF:
            text = "(PDF metni çıkarılamadı: pypdf kurulu değil. pip install pypdf)"
    else:
        text = raw.decode("utf-8", "replace")
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 6000))
    out.update({"document_id": doc_id, "mime_type": mime,
                "source_url": "https://mevzuat.adalet.gov.tr/ictihat/%s" % doc_id})
    return out


def _http_error(exc: HttpError) -> Dict[str, Any]:
    if exc.status == 429:
        return {"error": "Bedesten hız sınırı (10 istek / 30 sn). Birkaç saniye sonra tekrar deneyin.",
                "retry": True}
    return {"error": str(exc)}


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Arama ifadesi. Tırnaksız çok kelime önce tam ifade olarak aranır (bkz. kelime_modu). \"tam cümle\", +zorunlu, -hariç, AND/OR/NOT olduğu gibi gönderilir; tırnaklı ifadenin yanındaki işaretsiz kelime VEYA ile birleşir, hepsi geçsin diye +\"ifade\" +kelime yazın. Joker yok. Nokta, eğik çizgi, kesme gibi işaretler boşluğa çevrilir (2014/27629 → 2014 27629, 01.04.1974 → 01 04 1974); gönderilen biçim uygulanan_sorgu'da."},
        "kelime_modu": {"type": "string", "enum": ["ifade", "hepsi", "herhangi"], "default": "ifade",
                        "description": "Tırnaksız çok kelimeli sorgu: ifade = önce tam ifade, sonuç yoksa her kelime, o da yoksa herhangi biri (yanıtta genisleme_notu); hepsi = yalnız her kelimenin geçtiği kararlar, yoksa 0 döner, VEYA'ya genişletilmez; herhangi = Bedesten'in ham davranışı (VEYA, çok geniş)."},
        "courts": {"type": "array", "items": {"type": "string", "enum": list(COURT_TYPES)},
                   "description": "Mahkeme türleri. Varsayılan: Yargıtay + Danıştay."},
        "chamber": {"type": "string", "description": "Daire/kurul kodu: H1-H23, C1-C23, HGK, CGK, D1-D17, IDDK, VDDK, IBK … veya tam Türkçe adı. Boş = tümü."},
        "date_from": {"type": "string", "description": "Karar tarihi başlangıcı, YYYY-MM-DD"},
        "date_to": {"type": "string", "description": "Karar tarihi bitişi, YYYY-MM-DD"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10},
        "sort": {"type": "string", "enum": ["ilgi", "yeni", "eski"], "default": "ilgi",
                 "description": "ilgi = konuyla en ilgili karar önce (varsayılan); yeni = en yeni karar önce; "
                                "eski = en eski önce. Yerleşik içtihat çoğu zaman eski tarihlidir: konu aramasında "
                                "ilgi, güncel uygulamanın teyidinde yeni."},
    },
    "required": ["query"],
}

GET_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {"type": "string", "description": "ictihat_ara sonucundaki document_id"},
        "page": {"type": "integer", "minimum": 1, "default": 1, "description": "Uzun kararlar sayfalanır"},
        "page_chars": {"type": "integer", "default": 6000},
    },
    "required": ["document_id"],
}

SOURCE = Source(
    key="ictihat",
    label="Yargıtay · Danıştay · BAM · yerel mahkeme · KYB (Bedesten)",
    kind="ictihat",
    notes=(
        "Bedesten (mevzuat.adalet.gov.tr/ictihat) 5 mahkeme türünü tek uçta verir. Karar "
        "listesi METİN içermez; yorum yapmadan önce ictihat_getir ile metni okuyun. "
        "Sonuçlar ilgiye göre sıralanır (sort='ilgi'); en yeni karar için sort='yeni'. "
        "Hız sınırı: 10 istek/30 sn — art arda 5'ten fazla arama yapmayın. Alıntı: "
        "'Yargıtay 9. HD, E. 2023/1234, K. 2024/567, 12.03.2024' — citation alanı verbatim."
    ),
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage="https://mevzuat.adalet.gov.tr/ictihat",
)
