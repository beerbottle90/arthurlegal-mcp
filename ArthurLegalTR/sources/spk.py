"""SPK — Sermaye Piyasası Kurulu haftalık bültenleri (spk.gov.tr).

The Kurul's decisions are published in the weekly *SPK Bülteni* PDF, not as a
searchable database. Listing pages are server-rendered:

    /spk-bultenleri/<yyyy>-yili-spk-bultenleri?s=<n>   → links https://spk.gov.tr/data/<id>/<yyyy>-<no>.pdf

Two operations therefore exist: find a bülten by year/number/date, and search
*inside* one (download → text → split on numbered decision headings → rank).
The crawler indexes every decision section of every bülten so the local index
can answer subject queries across years.

Citation contract: ``SPK Bülteni 2026/27, <bölüm başlığı>`` — plus the Kurul
karar tarihi/sayısı quoted from the section text when present
(``Kurulumuzun 04.09.2026 tarih ve 45/1234 sayılı kararı``).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from net import Http, HttpError
from textx import paginate, pdf_to_text, strip_tags, count_hits, excerpt
from sources import Source

BASE = "https://spk.gov.tr"
_http = Http(BASE, {"Accept": "text/html,application/pdf,*/*"})
_list_cache: Dict[int, List[Dict[str, Any]]] = {}
_pdf_cache: Dict[str, str] = {}
_PDF = re.compile(r'href="(https://spk\.gov\.tr/data/[0-9a-f]+/(\d{4})-(\d+)\.pdf)"', re.I)
_SECTION = re.compile(r"(?m)^\s*([A-ZÇĞİÖŞÜ]\.\s+[A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ \-/,()]{6,})\s*$")


def _year_list(year: int) -> List[Dict[str, Any]]:
    if year in _list_cache:
        return _list_cache[year]
    items: Dict[str, Dict[str, Any]] = {}
    for s in range(1, 8):
        try:
            html = _http.get_text("/spk-bultenleri/%d-yili-spk-bultenleri" % year, params={"s": s} if s > 1 else None)
        except HttpError:
            break
        found = 0
        for m in _PDF.finditer(html):
            url, y, no = m.groups()
            key = "%s/%s" % (y, no)
            if key not in items:
                items[key] = {"bulten": key, "year": int(y), "number": int(no), "pdf_url": url,
                              "citation": "SPK Bülteni %s" % key}
                found += 1
        if not found:
            break
    out = sorted(items.values(), key=lambda x: -x["number"])
    _list_cache[year] = out
    return out


def _text(bulten: str) -> str:
    if bulten in _pdf_cache:
        return _pdf_cache[bulten]
    y, _, no = bulten.partition("/")
    entry = next((b for b in _year_list(int(y)) if b["number"] == int(no)), None)
    if not entry:
        raise HttpError(404, BASE, ("Bülten bulunamadı: %s" % bulten).encode())
    body, _, _ = _http.get(entry["pdf_url"], timeout=120)
    text, _ = pdf_to_text(body)
    if len(_pdf_cache) > 6:
        _pdf_cache.clear()
    _pdf_cache[bulten] = text
    return text


def _sections(text: str) -> List[Dict[str, str]]:
    parts = _SECTION.split(text)
    out = []
    if len(parts) > 1:
        for i in range(1, len(parts) - 1, 2):
            out.append({"heading": parts[i].strip(), "body": parts[i + 1].strip()})
    else:
        for i in range(0, len(text), 3000):
            out.append({"heading": "bölüm %d" % (i // 3000 + 1), "body": text[i:i + 3000]})
    return out


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    year = int(args.get("year") or datetime.now().year)
    try:
        items = _year_list(year)
    except HttpError as exc:
        return {"error": str(exc)}
    if args.get("number"):
        items = [b for b in items if b["number"] == int(args["number"])]
    return {"year": year, "total": len(items), "results": items[: int(args.get("limit") or 60)],
            "note": "Bülten içinde arama: spk_bulten_icinde_ara(bulten='2026/27', query=...). "
                    "Arşiv genelinde konu araması için yerel indeks (semantik_ara, kurum='spk')."}


def search_within(args: Dict[str, Any]) -> Dict[str, Any]:
    bulten = str(args.get("bulten") or "").strip().replace("-", "/")
    q = (args.get("query") or "").strip()
    if not re.match(r"^\d{4}/\d+$", bulten):
        return {"error": "bulten biçimi 'YYYY/N' olmalı, örn. '2026/27'."}
    try:
        text = _text(bulten)
    except HttpError as exc:
        return {"error": str(exc)}
    secs = _sections(text)
    terms = [t for t in q.split() if len(t) > 1]
    scored = []
    for s in secs:
        hits = sum(count_hits(s["heading"] + " " + s["body"], t) for t in terms) if terms else 1
        if hits:
            scored.append((hits, s))
    scored.sort(key=lambda x: -x[0])
    limit = max(1, min(int(args.get("limit") or 8), 30))
    return {"bulten": bulten, "query": q, "sections": len(secs), "matches": len(scored),
            "results": [{"heading": s["heading"], "hits": h, "excerpt": excerpt(s["body"], terms[0] if terms else "", 400),
                         "chars": len(s["body"])} for h, s in scored[:limit]],
            "citation": "SPK Bülteni %s" % bulten}


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    bulten = str(args.get("id") or "").strip().replace("-", "/")
    if not re.match(r"^\d{4}/\d+$", bulten):
        return {"error": "id biçimi 'YYYY/N' (bülten no) olmalı."}
    try:
        text = _text(bulten)
    except HttpError as exc:
        return {"error": str(exc)}
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"bulten": bulten, "citation": "SPK Bülteni %s" % bulten})
    return out


def crawl(index, years: Optional[List[int]] = None, limit: int = 0, log=print) -> Dict[str, Any]:
    n = 0
    for y in years or [datetime.now().year]:
        for b in _year_list(y):
            if limit and n >= limit:
                break
            try:
                text = _text(b["bulten"])
            except HttpError as exc:
                log("spk: %s failed: %s" % (b["bulten"], exc))
                continue
            dm = re.search(r"%s\s+(\d{2})/(\d{2})/(\d{4})" % re.escape(b["bulten"]), text[:3000])
            date = "%s-%s-%s" % (dm.group(3), dm.group(2), dm.group(1)) if dm else "%d-01-01" % y
            for i, s in enumerate(_sections(text)):
                index.upsert({"ref": "spk:%s:%d" % (b["bulten"], i), "title": "SPK Bülteni %s — %s" % (b["bulten"], s["heading"]),
                              "body": s["body"][:60000], "url": b["pdf_url"], "lang": "tr", "date": date,
                              "status": "bülten", "court": "SPK", "subject": "sermaye piyasası",
                              "citation": "SPK Bülteni %s, %s" % (b["bulten"], s["heading"]),
                              "meta": {"kurum": "spk", "bulten": b["bulten"]}})
                n += 1
            log("spk: %s indexed, %d sections" % (b["bulten"], n))
    return {"indexed": n}


SEARCH_SCHEMA = {"type": "object", "properties": {
    "year": {"type": "integer", "description": "Bülten yılı (varsayılan: bu yıl)"},
    "number": {"type": "integer", "description": "Bülten sayısı"},
    "limit": {"type": "integer", "default": 60}}}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string", "description": "Bülten no, örn. 2026/27"},
                                               "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}
WITHIN_SCHEMA = {"type": "object", "properties": {
    "bulten": {"type": "string", "description": "Örn. 2026/27"}, "query": {"type": "string"},
    "limit": {"type": "integer", "default": 8}}, "required": ["bulten", "query"]}

SOURCE = Source(
    key="spk", label="SPK — Sermaye Piyasası Kurulu haftalık bültenleri", kind="kurum",
    notes=("Kurul kararları haftalık bülten PDF'lerindedir. Önce kurum_karari_ara(kurum='spk', year=…) ile bülteni bulun, "
           "sonra spk_bulten_icinde_ara. Tebliğ/yönetmelik metinleri için mevzuat_ara (query='Sermaye Piyasası')."),
    search=search, get=get, crawl=crawl, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + "/spk-bultenleri",
)
