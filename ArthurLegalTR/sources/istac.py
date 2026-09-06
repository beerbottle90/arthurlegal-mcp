"""İSTAÇ — İstanbul Tahkim Merkezi kuralları (istac.org.tr).

ISTAC awards are confidential and not published; what the Centre publishes is
its **rules** — Tahkim Kuralları (2016, rev. 2020/2022), Arabuluculuk Kuralları,
Seri Tahkim, Acil Durum Hakemi, Divan ve Sekretarya çalışma usulleri, ücret
tarifesi. Those PDFs are the corpus here. ``/tr/kurallar`` lists them; the set
below is what that page linked on 2026-09 and is refreshed by the crawler.

Citation contract: ``İSTAÇ Tahkim Kuralları m. 12`` — article numbers from the
rule text, never from memory; rules were revised, so name the version/year the
PDF carries on its cover.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from net import Http, HttpError
from textx import paginate, pdf_to_text, strip_tags, count_hits, excerpt
from sources import Source

BASE = "https://istac.org.tr"
_http = Http(BASE, {"Accept": "text/html,application/pdf,*/*"})
# The 2016 "Tahkim ve Arabuluculuk Kuralları" and "Divan ve Sekretarya" PDFs are
# still linked from /tr/kurallar but return 404 (checked 2026-09); they are
# picked up dynamically if the Centre restores them, and skipped quietly if not.
KNOWN = [
    ("tahkim", "İSTAÇ Tahkim Kuralları (v3, 2020)", "https://istac.org.tr/wp-content/uploads/2020/12/istac_tahkim_kurallari_v3_TR.pdf"),
    ("kurallar_2022", "İSTAÇ Kurallar (2022 derlemesi)", "https://istac.org.tr/wp-content/uploads/2022/05/İSTAÇ-TR-01.pdf"),
]
_cache: Dict[str, str] = {}
_ART = re.compile(r"(?im)^\s*(MADDE\s+\d+[A-Za-z]?\s*[-–—:.]?.*)$")
_HYPHEN = re.compile(r"(\w)\s*-\s*\n\s*(\w)")


def _chunks(text: str, size: int = 1800) -> List[Dict[str, str]]:
    """Rule PDFs are typeset in two columns with soft hyphens at line ends and
    no 'MADDE n' headings — the article number sits inside the running text.
    Repair the hyphenation, then split on 'MADDE n' if present, else on the
    nearest short Title-Case line before each ~1800-char window."""
    text = _HYPHEN.sub(r"\1\2", text)
    parts = _ART.split(text)
    if len(parts) > 2:
        return [{"heading": parts[i].strip(), "body": parts[i + 1]} for i in range(1, len(parts) - 1, 2)]
    out: List[Dict[str, str]] = []
    heading = "metin"
    buf: List[str] = []
    n = 0
    for line in text.split("\n"):
        s = line.strip()
        if 3 < len(s) < 60 and not s.endswith((".", ",", ";", ":")) and s[:1].isupper() and not s[:1].isdigit() \
                and s.count(" ") <= 7 and s == s.title() or (s.isupper() and 3 < len(s) < 60):
            if buf and n > size // 2:
                out.append({"heading": heading, "body": "\n".join(buf)})
                buf, n = [], 0
            heading = s
        buf.append(line)
        n += len(line)
        if n >= size:
            out.append({"heading": heading, "body": "\n".join(buf)})
            buf, n = [], 0
    if buf:
        out.append({"heading": heading, "body": "\n".join(buf)})
    return out


def rules() -> List[Dict[str, str]]:
    """Known rule PDFs plus whatever /tr/kurallar links right now."""
    out = [{"id": k, "title": t, "url": u} for k, t, u in KNOWN]
    seen = {u for _, _, u in KNOWN}
    try:
        html = _http.get_text("/tr/kurallar")
        for m in re.finditer(r'<a[^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', html, re.S | re.I):
            url, inner = m.groups()
            if url in seen or "istac.org.tr" not in url:
                continue
            seen.add(url)
            title = strip_tags(inner) or url.rsplit("/", 1)[-1]
            out.append({"id": re.sub(r"[^a-z0-9]+", "_", title.lower())[:40], "title": title, "url": url})
    except HttpError:
        pass
    return out


def _text(rule: Dict[str, str]) -> str:
    if rule["url"] in _cache:
        return _cache[rule["url"]]
    body, _, _ = _http.get(rule["url"], timeout=120)
    text, _ = pdf_to_text(body)
    _cache[rule["url"]] = text
    return text


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    want = args.get("rule") or ""
    terms = [t for t in q.split() if len(t) > 1]
    results = []
    skipped = []
    lst = rules()
    if not q:
        return {"rules": lst, "note": "query verin: kural metinlerinde madde bazında arama yapılır."}
    for r in lst:
        if want and r["id"] != want:
            continue
        try:
            text = _text(r)
        except HttpError as exc:
            skipped.append({"rule": r["id"], "status": exc.status})
            continue
        for ch in _chunks(text):
            hits = sum(count_hits(ch["heading"] + " " + ch["body"], t) for t in terms)
            if hits:
                results.append({"rule": r["id"], "rule_title": r["title"], "heading": ch["heading"][:120], "hits": hits,
                                "excerpt": excerpt(ch["body"], terms[0], 300), "source_url": r["url"],
                                "citation": "%s, %s" % (r["title"], ch["heading"][:40])})
    results.sort(key=lambda x: -x.get("hits", 0))
    out = {"query": q, "total": len(results), "results": results[: int(args.get("limit") or 10)],
           "note": "İSTAÇ hakem kararları yayımlanmaz; bu kaynak yalnız kurallardır. Tahkim itirazı / tenfiz içtihadı için ictihat_ara."}
    if skipped:
        out["skipped_rules"] = skipped
    return out


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(args.get("id") or "").strip()
    rule = next((r for r in rules() if r["id"] == rid or r["url"] == rid), None)
    if not rule:
        return {"error": "Kural bulunamadı: %s. Mevcut: %s" % (rid, [r["id"] for r in rules()])}
    try:
        text = _text(rule)
    except HttpError as exc:
        return {"error": str(exc)}
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"rule": rule["id"], "title": rule["title"], "source_url": rule["url"]})
    return out


def crawl(index, log=print) -> Dict[str, Any]:
    n = 0
    for r in rules():
        try:
            text = _text(r)
        except HttpError as exc:
            log("istac: %s failed: %s" % (r["id"], exc))
            continue
        chunks = [(c["heading"], c["body"]) for c in _chunks(text)]
        for i, (h, b) in enumerate(chunks):
            index.upsert({"ref": "istac:%s:%d" % (r["id"], i), "title": "%s — %s" % (r["title"], h[:100]),
                          "body": b[:20000], "url": r["url"], "lang": "tr", "date": "", "status": "kural",
                          "court": "İSTAÇ", "subject": "tahkim", "citation": "%s, %s" % (r["title"], h[:60]),
                          "meta": {"kurum": "istac", "rule": r["id"]}})
            n += 1
        log("istac: %s, %d articles" % (r["id"], len(chunks)))
    return {"indexed": n}


SEARCH_SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string"}, "rule": {"type": "string", "description": "Kural kimliği (boş = hepsi)"},
    "limit": {"type": "integer", "default": 10}}}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string"}, "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="istac", label="İSTAÇ — İstanbul Tahkim Merkezi kuralları", kind="kurallar",
    notes="Kararlar gizlidir; yalnız kural metinleri. Madde numarası PDF'ten; sürüm yılı belirtilir.",
    search=search, get=get, crawl=crawl, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + "/tr/kurallar",
)
