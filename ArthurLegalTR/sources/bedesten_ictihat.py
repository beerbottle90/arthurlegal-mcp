"""Yargıtay, Danıştay, yerel hukuk, istinaf ve KYB kararları — Bedesten (Adalet Bakanlığı).

Upstream: ``https://bedesten.adalet.gov.tr/emsal-karar`` — the JSON backend of
``mevzuat.adalet.gov.tr/ictihat``. Two endpoints:

    POST /searchDocuments      {"data": {...}, "applicationName": "UyapMevzuat", "paging": true}
    POST /getDocumentContent   {"data": {"documentId": ...}, "applicationName": "UyapMevzuat"}

The document comes back base64-encoded as HTML or PDF. Measured rate limit
(2026-05, per source IP): 10 requests / 30 s. One request every 3.5 s keeps
clear of it; a 429 pauses the bucket for the server's Retry-After.

Search syntax accepted by ``phrase``: bare words (AND), ``"tam cümle"``,
``+zorunlu``, ``-hariç``, ``AND``/``OR``/``NOT``. No wildcards.

Citation contract: cite as ``Yargıtay 9. HD, E. 2023/1234, K. 2024/567,
12.03.2024`` using the *esasNo/kararNo/kararTarihiStr* fields verbatim; the
``source_url`` is ``https://mevzuat.adalet.gov.tr/ictihat/<documentId>``.

Ported from saidsurucu/yargi-mcp (MIT) — request shape, chamber map and the
rate-limit figures are his; the transport is rewritten on the standard library.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional

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


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    phrase = (args.get("query") or "").strip()
    if not phrase:
        return {"error": "query gerekli. Örnek: 'lisans iptali \"hizmet kusuru\" -tazminat'"}
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
        "sortFields": ["KARAR_TARIHI"],
        "sortDirection": "desc" if (args.get("sort") or "desc") == "desc" else "asc",
    }
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
    payload = {"data": data, "applicationName": "UyapMevzuat", "paging": True}
    try:
        body = _http.post_json("/emsal-karar/searchDocuments", payload)
    except HttpError as exc:
        return _http_error(exc)
    meta = body.get("metadata") or {}
    if meta.get("FMTY") != "SUCCESS":
        return {"error": "Bedesten: %s" % (meta.get("FMTE") or meta), "query": phrase}
    d = body.get("data") or {}
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
    return {
        "query": phrase, "courts": courts, "page": data["pageNumber"], "page_size": page_size,
        "total": d.get("total", 0), "results": items,
        "note": "Metin için ictihat_getir(document_id). Karar listesi metin içermez; "
                "alıntı için citation alanını birebir kullanın.",
    }


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
        "query": {"type": "string", "description": "Arama ifadesi. 'kelime', \"tam cümle\", +zorunlu, -hariç, AND/OR/NOT. Joker yok."},
        "courts": {"type": "array", "items": {"type": "string", "enum": list(COURT_TYPES)},
                   "description": "Mahkeme türleri. Varsayılan: Yargıtay + Danıştay."},
        "chamber": {"type": "string", "description": "Daire/kurul kodu: H1-H23, C1-C23, HGK, CGK, D1-D17, IDDK, VDDK, IBK … veya tam Türkçe adı. Boş = tümü."},
        "date_from": {"type": "string", "description": "Karar tarihi başlangıcı, YYYY-MM-DD"},
        "date_to": {"type": "string", "description": "Karar tarihi bitişi, YYYY-MM-DD"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10},
        "sort": {"type": "string", "enum": ["desc", "asc"], "default": "desc"},
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
        "Hız sınırı: 10 istek/30 sn — art arda 5'ten fazla arama yapmayın. Alıntı: "
        "'Yargıtay 9. HD, E. 2023/1234, K. 2024/567, 12.03.2024' — citation alanı verbatim."
    ),
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage="https://mevzuat.adalet.gov.tr/ictihat",
)
