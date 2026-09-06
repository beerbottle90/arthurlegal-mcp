"""Mevzuat — kanun, KHK, CB kararnamesi, yönetmelik, tebliğ … (Bedesten / mevzuat.adalet.gov.tr).

Upstream: ``https://bedesten.adalet.gov.tr/mevzuat`` (same rate limit and same
envelope as the içtihat service; the bucket is shared).

    POST /searchDocuments        title / full-text search, 12 legislation types
    POST /getDocumentContent     {"documentType": "MEVZUAT", "id": mevzuatId}
    POST /mevzuatMaddeTree       article tree (içindekiler)
    POST /getDocumentContent     {"documentType": "MADDE", "id": maddeId}
    POST /getDocumentContent     {"documentType": "GEREKCE", "id": gerekceId}

Citation contract: ``<Kanun adı> (No. 6446, RG 30.03.2013/28603) m. 12/3``.
``mevzuatNo``, ``resmiGazeteTarihi`` and ``resmiGazeteSayisi`` come verbatim from
the record; article numbers come from the madde tree — never from memory.

Ported from saidsurucu/mevzuat-mcp (MIT) — endpoint discovery and envelope are
his; the transport is rewritten on the standard library and the per-type tool
sprawl is collapsed into one ``mevzuat_ara`` with a ``types`` filter.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from net import Http, HttpError
from textx import html_to_text, paginate, pdf_to_text, tr_fold, excerpt, count_hits
from sources import Source
from sources.bedesten_ictihat import BUCKET, HEADERS

BASE = "https://bedesten.adalet.gov.tr/mevzuat"
_http = Http(BASE, HEADERS, bucket=BUCKET)

TYPES = {
    "KANUN": "Kanun", "KHK": "Kanun Hükmünde Kararname", "TUZUK": "Tüzük",
    "CB_KARARNAME": "Cumhurbaşkanlığı Kararnamesi", "CB_KARAR": "Cumhurbaşkanı Kararı",
    "CB_YONETMELIK": "Cumhurbaşkanlığı / Bakanlar Kurulu Yönetmeliği",
    "CB_GENELGE": "Cumhurbaşkanlığı Genelgesi", "YONETMELIK": "Bakanlar Kurulu Yönetmeliği",
    "KKY": "Kurum ve Kuruluş Yönetmeliği", "UY": "Üniversite Yönetmeliği",
    "TEBLIGLER": "Tebliğ", "MULGA": "Mülga mevzuat",
}

_cache: Dict[str, Any] = {}


def _wrap(data: Dict[str, Any], paging: bool = False) -> Dict[str, Any]:
    out = {"data": data, "applicationName": "UyapMevzuat"}
    if paging:
        out["paging"] = True
    return out


def _rg_iso(d: str, end: bool = False) -> str:
    """DD/MM/YYYY or YYYY-MM-DD → the UTC boundary the API expects (Turkey is UTC+3)."""
    d = d.strip()
    dt = datetime.strptime(d, "%Y-%m-%d") if "-" in d[:5] else datetime.strptime(d, "%d/%m/%Y")
    if end:
        return dt.strftime("%Y-%m-%dT21:00:00.000Z")
    return (dt - timedelta(days=1)).strftime("%Y-%m-%dT21:00:00.000Z")


def _call(path: str, data: Dict[str, Any], paging: bool = False) -> Any:
    body = _http.post_json(path, _wrap(data, paging))
    meta = body.get("metadata") or {}
    if meta.get("FMTY") != "SUCCESS":
        raise HttpError(400, BASE + path, str(meta.get("FMTE") or meta).encode())
    return body.get("data")


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    number = str(args.get("number") or "").strip()
    if not q and not number:
        return {"error": "query veya number gerekli."}
    types = args.get("types") or []
    if isinstance(types, str):
        types = [t.strip() for t in types.split(",") if t.strip()]
    bad = [t for t in types if t not in TYPES]
    if bad:
        return {"error": "Bilinmeyen mevzuat türü %s. Geçerli: %s" % (bad, list(TYPES))}
    where = args.get("search_in") or "title"
    data: Dict[str, Any] = {
        "pageSize": max(1, min(int(args.get("page_size") or 10), 50)),
        "pageNumber": max(1, int(args.get("page") or 1)),
        "sortFields": ["RESMI_GAZETE_TARIHI"],
        "sortDirection": "desc",
    }
    if q:
        if where == "fulltext":
            data["phrase"] = q
            data["basliktaAra"] = False
        else:
            data["mevzuatAdi"] = q
    if number:
        data["mevzuatNo"] = number
    if types:
        data["mevzuatTurList"] = types
    if args.get("exact_phrase"):
        data["tamCumle"] = True
    try:
        if args.get("rg_date_from"):
            data["resmiGazeteTarihiStart"] = _rg_iso(args["rg_date_from"])
        if args.get("rg_date_to"):
            data["resmiGazeteTarihiEnd"] = _rg_iso(args["rg_date_to"], end=True)
    except ValueError:
        return {"error": "Tarih biçimi YYYY-MM-DD veya DD/MM/YYYY olmalı."}
    if args.get("rg_number"):
        data["resmiGazeteSayisi"] = str(args["rg_number"])
    try:
        d = _call("/searchDocuments", data, paging=True) or {}
    except HttpError as exc:
        return {"error": str(exc)}
    items = []
    for m in d.get("mevzuatList") or []:
        tur = m.get("mevzuatTur") or {}
        rg_date = (m.get("resmiGazeteTarihi") or "")[:10]
        items.append({
            "mevzuat_id": m.get("mevzuatId"),
            "number": m.get("mevzuatNo"),
            "title": (m.get("mevzuatAdi") or "").strip(),
            "type": tur.get("name"),
            "tertip": m.get("mevzuatTertip"),
            "rg_date": rg_date,
            "rg_number": m.get("resmiGazeteSayisi"),
            "mukerrer": m.get("mukerrer"),
            "gerekce_id": m.get("gerekceId"),
            "citation": _citation(m, tur, rg_date),
            "source_url": m.get("url") or "",
        })
    return {"query": q or number, "total": d.get("total", 0), "page": data["pageNumber"],
            "results": items,
            "note": "Tam metin: mevzuat_getir(mevzuat_id). Madde ağacı: mevzuat_icindekiler. "
                    "Belirli hükmü aramak için mevzuat_icinde_ara."}


def _citation(m: Dict[str, Any], tur: Dict[str, Any], rg_date: str) -> str:
    title = (m.get("mevzuatAdi") or "").strip()
    no = m.get("mevzuatNo")
    s = title
    if no:
        s += " (%s No. %s" % (TYPES.get(tur.get("name") or "", tur.get("name") or ""), no)
        if rg_date:
            s += ", RG %s/%s" % (_tr_date(rg_date), m.get("resmiGazeteSayisi") or "?")
        s += ")"
    return s


def _tr_date(iso: str) -> str:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso


def _content(doc_type: str, doc_id: str) -> Dict[str, Any]:
    key = "%s:%s" % (doc_type, doc_id)
    if key in _cache:
        return _cache[key]
    d = _call("/getDocumentContent", {"documentType": doc_type, "id": doc_id}) or {}
    raw = base64.b64decode(d.get("content") or b"")
    mime = d.get("mimeType") or "text/html"
    if "pdf" in mime:
        text, _ = pdf_to_text(raw)
    else:
        text = html_to_text(raw.decode("utf-8", "replace"))
    out = {"text": text, "mime": mime}
    if len(_cache) > 64:
        _cache.clear()
    _cache[key] = out
    return out


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(args.get("mevzuat_id") or "").strip()
    if not mid:
        return {"error": "mevzuat_id gerekli (mevzuat_ara sonucundan)."}
    try:
        c = _content("MEVZUAT", mid)
    except HttpError as exc:
        return {"error": str(exc)}
    out = paginate(c["text"], args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"mevzuat_id": mid, "mime_type": c["mime"]})
    return out


def toc(args: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(args.get("mevzuat_id") or "").strip()
    if not mid:
        return {"error": "mevzuat_id gerekli."}
    try:
        d = _call("/mevzuatMaddeTree", {"mevzuatId": mid}) or {}
    except HttpError as exc:
        return {"error": str(exc)}
    nodes: List[Dict[str, Any]] = []

    def walk(n: Dict[str, Any], depth: int) -> None:
        if not isinstance(n, dict):
            return
        title = (n.get("title") or n.get("maddeBaslik") or n.get("description") or "").strip()
        mno = n.get("maddeNo")
        if title or mno:
            nodes.append({"madde_id": n.get("maddeId") or n.get("id"), "madde_no": mno,
                          "title": title[:200], "depth": depth, "gerekce_id": n.get("gerekceId")})
        for ch in n.get("children") or []:
            walk(ch, depth + 1)

    if isinstance(d, list):
        for n in d:
            walk(n, 0)
    else:
        walk(d, 0)
    return {"mevzuat_id": mid, "total": len(nodes), "articles": nodes,
            "note": "Madde metni: mevzuat_madde_getir(madde_id)."}


def article(args: Dict[str, Any]) -> Dict[str, Any]:
    aid = str(args.get("madde_id") or "").strip()
    if not aid:
        return {"error": "madde_id gerekli (mevzuat_icindekiler sonucundan)."}
    try:
        c = _content("MADDE", aid)
    except HttpError as exc:
        return {"error": str(exc)}
    return {"madde_id": aid, "text": c["text"], "mime_type": c["mime"]}


def gerekce(args: Dict[str, Any]) -> Dict[str, Any]:
    gid = str(args.get("gerekce_id") or "").strip()
    if not gid:
        return {"error": "gerekce_id gerekli (mevzuat_ara sonucunda gerekce_id alanı; yoksa gerekçe yayımlanmamış)."}
    key = "GEREKCE:%s" % gid
    if key not in _cache:
        try:
            d = _call("/getGerekceContent", {"gerekceId": gid}) or {}
        except HttpError as exc:
            return {"error": str(exc)}
        raw = base64.b64decode(d.get("content") or b"")
        mime = d.get("mimetype") or d.get("mimeType") or "text/html"
        text = pdf_to_text(raw)[0] if "pdf" in mime else html_to_text(raw.decode("utf-8", "replace"))
        _cache[key] = {"text": text, "mime": mime, "mevzuat_id": d.get("mevzuatId")}
    c = _cache[key]
    out = paginate(c["text"], args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"gerekce_id": gid, "mevzuat_id": c.get("mevzuat_id"), "mime_type": c["mime"]})
    return out


_ART_SPLIT = re.compile(r"(?im)^\s*((?:ek\s+|geçici\s+)?madde\s+\d+[a-zA-Z]?(?:\s*/\s*\d+)?\s*[-–—:.]?)")


def search_within(args: Dict[str, Any]) -> Dict[str, Any]:
    """Split the full text on 'MADDE n' headings and rank articles by hits."""
    mid = str(args.get("mevzuat_id") or "").strip()
    q = (args.get("query") or "").strip()
    if not mid or not q:
        return {"error": "mevzuat_id ve query gerekli."}
    try:
        c = _content("MEVZUAT", mid)
    except HttpError as exc:
        return {"error": str(exc)}
    text = c["text"]
    parts = _ART_SPLIT.split(text)
    chunks: List[Dict[str, Any]] = []
    if len(parts) > 1:
        for i in range(1, len(parts) - 1, 2):
            head = parts[i].strip()
            body = parts[i + 1].strip()
            chunks.append({"heading": head, "body": body})
    else:
        step = 2500
        chunks = [{"heading": "bölüm %d" % (i // step + 1), "body": text[i:i + step]}
                  for i in range(0, len(text), step)]
    terms = [t for t in re.split(r"\s+", q) if len(t) > 1]
    scored = []
    for ch in chunks:
        hits = sum(count_hits(ch["body"] + " " + ch["heading"], t) for t in terms)
        if hits:
            scored.append((hits, ch))
    scored.sort(key=lambda x: -x[0])
    limit = max(1, min(int(args.get("limit") or 8), 30))
    out = [{"heading": ch["heading"], "hits": h, "excerpt": excerpt(ch["body"], terms[0] if terms else q, 300),
            "chars": len(ch["body"])} for h, ch in scored[:limit]]
    return {"mevzuat_id": mid, "query": q, "articles_scanned": len(chunks), "matches": len(scored),
            "results": out,
            "note": "Kavramsal (anahtar kelime paylaşmayan) hüküm için semantik_ara kullanın; "
                    "burada yalnız kelime eşleşmesi vardır."}


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Mevzuat adı (varsayılan) veya tam metin ifadesi."},
        "number": {"type": "string", "description": "Mevzuat numarası, örn. 6446"},
        "types": {"type": "array", "items": {"type": "string", "enum": list(TYPES)},
                  "description": "Tür filtresi. Boş = tümü."},
        "search_in": {"type": "string", "enum": ["title", "fulltext"], "default": "title"},
        "exact_phrase": {"type": "boolean", "default": False},
        "rg_date_from": {"type": "string", "description": "Resmî Gazete tarihi ≥, YYYY-MM-DD"},
        "rg_date_to": {"type": "string", "description": "Resmî Gazete tarihi ≤, YYYY-MM-DD"},
        "rg_number": {"type": "string", "description": "Resmî Gazete sayısı"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    },
}

GET_SCHEMA = {
    "type": "object",
    "properties": {
        "mevzuat_id": {"type": "string"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_chars": {"type": "integer", "default": 8000},
    },
    "required": ["mevzuat_id"],
}

SOURCE = Source(
    key="mevzuat",
    label="Mevzuat — kanun, KHK, CBK, yönetmelik, tebliğ (Bedesten)",
    kind="mevzuat",
    notes=(
        "12 mevzuat türü tek uçta. Varsayılan arama BAŞLIKTA; hüküm metni için "
        "search_in='fulltext' veya önce mevzuat_ara ile belgeyi bulup mevzuat_icinde_ara. "
        "Madde numarası ve RG künyesi kayıttan gelir; ezberden madde numarası yazmayın. "
        "Sektörel ikincil düzenleme (EPDK/SPK/BDDK tebliğ ve yönetmelikleri) de buradadır: "
        "types=['KKY','TEBLIGLER'] ve query='Enerji Piyasası' gibi."
    ),
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage="https://mevzuat.adalet.gov.tr",
)
