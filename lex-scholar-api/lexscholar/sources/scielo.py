"""SciELO — Latin America / Iberia, and the federation's full-text engine.

SciELO's real value here is **full text**: unlike every other no-auth source in
this server, ``www.scielo.br/j/{acron}/a/{pid}/?format=xml`` returns real JATS
with a ``<body>`` (verified 129,890 bytes) and ``?format=pdf`` returns the PDF
(461,536 bytes). So SciELO both searches *and* upgrades hits found elsewhere:
a DOAJ record whose link points at SciELO can be turned into readable text.

Verified: 26,003 law articles across ~70 journals in 36 collections. Law volume
by country — Chile 7,421 · Mexico ~4,250 · Brazil 4,228 · Colombia 3,052 ·
Costa Rica 1,594 · Venezuela 1,191 · Argentina 945 · Portugal 824.

Three upstream facts this module works around:

- ``search.scielo.org`` returns **403 to every automated client**, so discovery
  runs over ArticleMeta (journal directory + identifiers), never the search site.
- the legacy ``scielo.br/oai/scielo-oai.php`` is **dead (404)**; ArticleMeta
  replaced it.
- ArticleMeta's own ``format=xmlrsps`` gives ``<front>``+``<back>`` only — no
  body — so full text is fetched from the public site URL instead.
- collections differ: ``scielo.br`` serves full text fine, ``scielo.cl`` sits
  behind Cloudflare and 403s. Failures are reported, not hidden.

The subject label lives in ``v440`` and is **free text with variants**
(``DERECHO``, ``DIREITO``, ``DERECHO INTERNACIONAL`` ...), so law journals are
matched by substring rather than a clean code.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from .._http import get_json, get_text, LexScholarError
from .._text import jats_body_text, markup_to_text
from ..record import make_record

NAME = "scielo"
API = "https://articlemeta.scielo.org/api/v1"
SITE = "https://www.scielo.br"

# Substrings that mark a law journal in the free-text v440 subject field.
_LAW_TOKENS = ("DIREITO", "DERECHO", "LAW", "JURIS", "LEGAL")

# Collections worth scanning by default, most law-productive first.
DEFAULT_COLLECTIONS = ("scl", "chl", "mex", "col", "arg", "ven", "cri", "prt", "esp")

META = {
    "title": "SciELO (Latin America & Iberia)",
    "auth": "none",
    "peer_reviewed": True,          # SciELO curates for editorial quality
    "full_text": True,              # JATS <body> + PDF, verified
    "languages": ["es", "pt", "en"],
    "countries": ["BR", "CL", "MX", "CO", "AR", "VE", "CR", "PT", "ES", "PE", "BO", "EC", "UY"],
    "law_filter": "journal v440 contains DIREITO/DERECHO",
    "volume": "26,003 law articles / ~70 journals",
    "cost": "medium",
    "notes": "Only no-auth source with real full text. search.scielo.org is "
             "403 to bots; discovery runs over ArticleMeta.",
}

_journal_cache: Dict[str, List[Dict[str, Any]]] = {}


def law_journals(collection: str = "scl") -> List[Dict[str, Any]]:
    """Law journals in a collection (cached; the directory is a big payload)."""
    if collection in _journal_cache:
        return _journal_cache[collection]
    try:
        data = get_json(API, "/journal/", {"collection": collection}, timeout=60)
    except LexScholarError:
        _journal_cache[collection] = []
        return []
    out: List[Dict[str, Any]] = []
    for j in data if isinstance(data, list) else []:
        subjects = " ".join(str(s).upper() for s in (j.get("v441") or []) + (j.get("v440") or []))
        if any(tok in subjects for tok in _LAW_TOKENS):
            out.append({
                "issn": (j.get("v400") or [{}])[0].get("_") if isinstance(j.get("v400"), list) else None,
                "acron": (j.get("v68") or [{}])[0].get("_") if isinstance(j.get("v68"), list) else None,
                "title": (j.get("v100") or [{}])[0].get("_") if isinstance(j.get("v100"), list) else None,
                "collection": collection,
            })
    _journal_cache[collection] = out
    return out


def search(
    query: str = "",
    *,
    limit: int = 20,
    country: Optional[str] = None,
    collections: Optional[List[str]] = None,
    max_journals: int = 3,
    **_: Any,
) -> Dict[str, Any]:
    """Bounded discovery over SciELO law journals.

    ArticleMeta has no free-text endpoint, so this scans recent articles from
    the best-matching law journals and filters client-side. It is deliberately
    capped; SciELO's strongest role in the federation is ``fulltext()``.
    """
    cols = collections or ([_country_to_collection(country)] if country else list(DEFAULT_COLLECTIONS[:3]))
    terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]

    # Collect candidate PIDs first, then fetch the records concurrently —
    # ArticleMeta needs one request per article, so serial fetching dominated
    # the wall clock before this.
    candidates: List[Any] = []
    scanned_journals = 0
    for col in cols:
        for journal in law_journals(col)[:max_journals]:
            issn = journal.get("issn")
            if not issn:
                continue
            scanned_journals += 1
            try:
                ident = get_json(API, "/article/identifiers/",
                                 {"collection": col, "issn": issn, "limit": 30}, timeout=40)
            except LexScholarError:
                continue
            for item in (ident.get("objects") or [])[:30]:
                if item.get("code"):
                    candidates.append((item["code"], col))

    results: List[Dict[str, Any]] = []
    if candidates:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for rec in pool.map(lambda pair: _safe_get(pair[0], pair[1]), candidates[:60]):
                if rec and _matches(rec, terms):
                    results.append(rec)
                    if len(results) >= limit:
                        break
    return {"total": None, "scanned_journals": scanned_journals,
            "scanned_articles": len(candidates[:60]),
            "collections": cols, "results": results}


def get(code: str, *, collection: str = "scl", **_: Any) -> Dict[str, Any]:
    """Fetch one article record by SciELO PID."""
    data = get_json(API, "/article/", {"collection": collection, "code": code, "format": "json"},
                    timeout=40)
    return _to_record(data, collection)


def fulltext(code: str, *, collection: str = "scl", lang: str = "", **_: Any) -> str:
    """Return the article body as clean text.

    This is the module's headline capability: it turns a SciELO identifier
    (including one discovered via DOAJ or OpenAIRE) into readable body text.

    The URL is taken from the record's own ``fulltexts`` map rather than
    constructed. The modern ``/j/{acron}/a/{id}/`` form needs SciELO's internal
    document id, **not** the PID, so building it from the PID 404s; the API
    hands us a working link instead. XML is parsed as JATS, HTML as markup.
    """
    rec = get(code, collection=collection)
    links: Dict[str, Any] = rec.get("_scielo_fulltexts") or {}

    candidates: List[str] = []
    for kind in ("xml", "html"):
        entry = links.get(kind)
        if isinstance(entry, dict):
            if lang and entry.get(lang):
                candidates.append(entry[lang])
            candidates.extend(v for v in entry.values() if isinstance(v, str))
        elif isinstance(entry, str):
            candidates.append(entry)

    if not candidates:
        raise LexScholarError("SciELO exposes no full-text link for %s" % code)

    last: Optional[Exception] = None
    for url in candidates:
        try:
            markup = get_text(url, timeout=60)
        except Exception as exc:          # per-collection guards (e.g. scielo.cl)
            last = exc
            continue
        if url.endswith("xml") or "<article" in markup[:5000]:
            text = jats_body_text(markup)
        else:
            # An HTML article page also carries the site chrome (menus, journal
            # lists). Drop everything before the article title so the caller
            # gets the article, not SciELO's navigation.
            text = _strip_chrome(markup_to_text(markup), rec.get("title"))
        if text and len(text) > 200:
            return text
    raise LexScholarError("could not fetch SciELO full text for %s: %s" % (code, last))


def _strip_chrome(text: str, title: Optional[str]) -> str:
    """Slice an HTML page's text down to the article, using the title as anchor."""
    if not title:
        return text
    anchor = " ".join(title.split()[:6])
    if not anchor:
        return text
    idx = text.find(anchor)
    if idx == -1:                       # try again case-insensitively
        idx = text.lower().find(anchor.lower())
    return text[idx:] if idx > 0 else text


def _safe_get(code: str, collection: str) -> Optional[Dict[str, Any]]:
    try:
        return get(code, collection=collection)
    except LexScholarError:
        return None


def _matches(rec: Dict[str, Any], terms: List[str]) -> bool:
    """Require most query terms, not just one.

    ``any()`` was far too loose here: every article in a law journal contains
    "direito"/"derecho", so a single common term matched the whole corpus and
    returned noise. Short queries must match fully; longer ones need a majority.
    """
    if not terms:
        return True
    hay = " ".join(str(rec.get(f) or "") for f in
                   ("title", "abstract", "journal", "subjects")).lower()
    hits = sum(1 for t in terms if t in hay)
    needed = len(terms) if len(terms) <= 2 else (len(terms) + 1) // 2
    return hits >= needed


def _country_to_collection(country: Optional[str]) -> str:
    return {
        "BR": "scl", "CL": "chl", "MX": "mex", "CO": "col", "AR": "arg",
        "VE": "ven", "CR": "cri", "PT": "prt", "ES": "esp", "PE": "per",
        "BO": "bol", "EC": "ecu", "UY": "ury", "CU": "cub",
    }.get((country or "").upper(), "scl")


def _sub(field: Any, key: str = "_") -> Optional[str]:
    """ArticleMeta wraps most values as ``[{"_": "value"}]``."""
    if isinstance(field, list) and field:
        first = field[0]
        if isinstance(first, dict):
            return first.get(key)
        return str(first)
    if isinstance(field, dict):
        return field.get(key)
    return field if isinstance(field, str) else None


def _to_record(data: Dict[str, Any], collection: str) -> Dict[str, Any]:
    art = data.get("article") or data
    # The journal-level block ("title") carries the acronym and journal name;
    # they are not on the article record itself.
    journal_meta = data.get("title") or {}
    title = _sub(art.get("v12"))
    abstract = _sub(art.get("v83"))
    authors: List[str] = []
    for a in art.get("v10") or []:
        if isinstance(a, dict):
            name = " ".join(x for x in (a.get("n"), a.get("s")) if x)
            if name.strip():
                authors.append(name.strip())

    code = _sub(art.get("v880")) or _sub(art.get("v881")) or data.get("code")
    acron = _sub(journal_meta.get("v68")) or _sub(art.get("v68"))
    fulltexts = data.get("fulltexts") or {}

    # Prefer the link SciELO itself publishes; only fall back to a constructed
    # journal URL, which is a browse page rather than the article.
    landing = None
    for kind in ("html", "pdf"):
        entry = fulltexts.get(kind)
        if isinstance(entry, dict) and entry:
            landing = next(iter(entry.values()))
            break
        if isinstance(entry, str):
            landing = entry
            break
    if not landing and acron and code:
        landing = "%s/j/%s/" % (SITE, acron)

    pdf_entry = fulltexts.get("pdf")
    pdf_url = None
    if isinstance(pdf_entry, dict) and pdf_entry:
        pdf_url = next(iter(pdf_entry.values()))
    elif isinstance(pdf_entry, str):
        pdf_url = pdf_entry

    rec = make_record(
        source=NAME,
        id=code,
        doi=_sub(art.get("v237")),
        title=title,
        abstract=abstract,
        authors=authors,
        journal=_sub(journal_meta.get("v100")) or _sub(art.get("v30")),
        publisher=_sub(art.get("v62")),
        year=_sub(art.get("v65")) or _sub(art.get("v64")),
        language=_sub(art.get("v40")),
        country=_collection_to_country(collection),
        subjects=[s for s in ((art.get("v441") or []) + (art.get("v440") or []))
                  if isinstance(s, str)][:5],
        is_oa=True,
        peer_reviewed=True,
        pdf_url=pdf_url,
        landing_url=landing,
        license=_sub(art.get("v540")),
    )
    rec["_scielo_acron"] = acron
    rec["_scielo_collection"] = collection
    rec["_scielo_fulltexts"] = fulltexts
    return rec


def _collection_to_country(collection: str) -> Optional[str]:
    return {
        "scl": "BR", "chl": "CL", "mex": "MX", "col": "CO", "arg": "AR",
        "ven": "VE", "cri": "CR", "prt": "PT", "esp": "ES", "per": "PE",
        "bol": "BO", "ecu": "EC", "ury": "UY", "cub": "CU",
    }.get(collection)
