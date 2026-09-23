"""KVKK — Kişisel Verileri Koruma Kurulu karar özetleri (kvkk.gov.tr).

The reference implementation searches KVKK through the Brave web-search API,
i.e. with a paid key. This adapter does not: the Kurul Karar Özetleri listing
(``/Icerik/5406/kurul-karar-ozetleri?page=N``, 36 pages, ten per page) is
plain server-rendered HTML with links of the form ``/Icerik/<id>/<yyyy>-<no>``.
It is crawled into the local index (title + summary + full text) and searched
there, hybrid and — with an embeddings backend — semantically. ``search`` with
no local index falls back to scanning the listing pages live, which is slower
and keyword-only.

Citation contract: ``KVK Kurulu, 27.02.2024 tarih ve 2024/347 sayılı karar özeti``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from net import Http, HttpError
from textx import html_to_text, paginate, strip_tags, count_hits, excerpt
from sources import Source

BASE = "https://www.kvkk.gov.tr"
LIST = "/Icerik/5406/kurul-karar-ozetleri"
_http = Http(BASE, {"Accept": "text/html,*/*"})
_LINK = re.compile(r'<a[^>]*href="(?:https://www\.kvkk\.gov\.tr)?(/Icerik/(\d+)/(\d{4}-\d+))"', re.I)


def _listing(page: int) -> List[Dict[str, Any]]:
    html = _http.get_text(LIST, params={"page": page})
    items: List[Dict[str, Any]] = []
    seen = set()
    # Each card: a block containing the decision title text and the "Devamını Gör" link.
    for block in re.split(r'(?=<div[^>]*class="[^"]*(?:card|item|karar|list)[^"]*")', html):
        m = _LINK.search(block)
        if not m:
            continue
        href, cid, no = m.groups()
        if cid in seen:
            continue
        seen.add(cid)
        txt = strip_tags(block)
        txt = re.sub(r"\bDevamını Gör\b", "", txt).strip()
        dm = re.search(r"(\d{2}[./]\d{2}[./]\d{4})", txt)
        items.append({"id": cid, "decision_no": no.replace("-", "/"), "date": dm.group(1) if dm else "",
                      "summary": txt[:700], "source_url": BASE + href})
    if not items:  # fallback: bare links
        for m in _LINK.finditer(html):
            href, cid, no = m.groups()
            if cid not in seen:
                seen.add(cid)
                items.append({"id": cid, "decision_no": no.replace("-", "/"), "date": "", "summary": "",
                              "source_url": BASE + href})
    for it in items:
        it["citation"] = "KVK Kurulu, %s tarih ve %s sayılı karar özeti" % (it["date"] or "?", it["decision_no"])
    return items


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    max_pages = max(1, min(int(args.get("scan_pages") or 3), 10))
    hits: List[Dict[str, Any]] = []
    scanned = 0
    try:
        for p in range(1, max_pages + 1):
            for it in _listing(p):
                scanned += 1
                if not q or count_hits(it["summary"] + " " + it["decision_no"], q) or all(
                        count_hits(it["summary"], t) for t in q.split()):
                    hits.append({**it, "excerpt": excerpt(it["summary"], q.split()[0] if q else "", 200)})
    except HttpError as exc:
        return {"error": str(exc)}
    return {"query": q, "scanned": scanned, "total": len(hits), "results": hits[: int(args.get("limit") or 20)],
            "note": "Canlı tarama son %d liste sayfası (≈%d özet) ile sınırlıdır; tüm arşiv için yerel "
                    "indeks (semantik_ara, kurum='kvkk'). Tam metin: kurum_karari_getir(kurum='kvkk', id=id)." % (max_pages, max_pages * 10)}


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    cid = str(args.get("id") or "").strip()
    if not cid:
        return {"error": "id gerekli."}
    url = cid if cid.startswith("http") else "%s/Icerik/%s/x" % (BASE, cid)
    try:
        html = _http.get_text(url)
    except HttpError as exc:
        return {"error": str(exc)}
    m = re.search(r'<div[^>]*class="[^"]*(?:icerik|content|blog-detail|page-content)[^"]*"[^>]*>(.*?)<footer', html, re.S | re.I)
    text = html_to_text(m.group(1) if m else html)
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"id": cid, "source_url": url})
    return out


def crawl(index, max_pages: int = 40, fetch_text: bool = True, log=print) -> Dict[str, Any]:
    n = 0
    for p in range(1, max_pages + 1):
        try:
            items = _listing(p)
        except HttpError as exc:
            log("kvkk: page %d failed: %s" % (p, exc))
            break
        if not items:
            break
        for it in items:
            if getattr(index, "exists", lambda r: False)("kvkk:%s" % it["id"]):
                continue
            body = it["summary"]
            if fetch_text:
                g = get({"id": it["id"], "page_chars": 200000})
                body = g.get("text") or body
            index.upsert({"ref": "kvkk:%s" % it["id"], "title": "KVKK Kurul Kararı %s" % it["decision_no"],
                          "body": body, "url": it["source_url"], "lang": "tr", "date": _iso(it["date"]),
                          "status": "karar özeti", "court": "KVK Kurulu", "subject": "kişisel veriler",
                          "citation": it["citation"], "meta": {"kurum": "kvkk", "decision_no": it["decision_no"]}})
            n += 1
        log("kvkk: page %d, %d docs" % (p, n))
    return {"indexed": n}


def _iso(d: str) -> str:
    m = re.match(r"(\d{2})[./](\d{2})[./](\d{4})", d or "")
    return "%s-%s-%s" % (m.group(3), m.group(2), m.group(1)) if m else ""


SEARCH_SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string"}, "scan_pages": {"type": "integer", "default": 3, "maximum": 10},
    "limit": {"type": "integer", "default": 20}}}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string", "description": "İçerik id veya tam URL"},
                                               "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="kvkk", label="KVKK — Kişisel Verileri Koruma Kurulu karar özetleri", kind="kurum",
    notes="Kurul karar özetleri (~360). Canlı arama son sayfaları tarar; arşiv için yerel indeks + semantik_ara.",
    search=search, get=get, crawl=crawl, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + LIST,
)
