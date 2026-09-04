"""The normalized article record — the contract that unifies all sources.

Every adapter returns this exact shape, so an agent can search DOAJ, SciELO and
HAL in one call and get back one comparable list. Fields that a given upstream
does not provide are ``None`` rather than absent, so consumers never have to
guess whether a key exists.

The two fields that matter most for legal work are ``peer_reviewed`` and
``citation``:

- ``peer_reviewed`` is tri-state on purpose. ``True`` means the source
  guarantees it (DOAJ only indexes peer-reviewed OA journals and exposes the
  actual review process; HAL and OpenAIRE carry explicit flags). ``False``
  means it is known NOT to be peer reviewed (preprint servers, US
  student-edited law reviews). ``None`` means unknown — never assume.
- ``citation`` is a ready-to-paste attribution string. Several upstreams are
  CC BY-SA, so attribution is a licence obligation, not a nicety.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Canonical field order (also the documented response shape).
FIELDS = (
    "source", "id", "doi", "title", "abstract", "authors", "journal",
    "publisher", "year", "language", "country", "subjects",
    "is_oa", "peer_reviewed", "pdf_url", "landing_url", "license", "citation",
)


def make_record(
    *,
    source: str,
    id: Optional[str] = None,
    doi: Optional[str] = None,
    title: Optional[str] = None,
    abstract: Optional[str] = None,
    authors: Optional[List[str]] = None,
    journal: Optional[str] = None,
    publisher: Optional[str] = None,
    year: Optional[Any] = None,
    language: Optional[str] = None,
    country: Optional[str] = None,
    subjects: Optional[List[str]] = None,
    is_oa: Optional[bool] = None,
    peer_reviewed: Optional[bool] = None,
    pdf_url: Optional[str] = None,
    landing_url: Optional[str] = None,
    license: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one normalized record, with a generated ``citation``."""
    rec: Dict[str, Any] = {
        "source": source,
        "id": _clean_str(id),
        "doi": _clean_doi(doi),
        "title": _clean_str(title),
        "abstract": _clean_str(abstract),
        "authors": [a for a in (authors or []) if a],
        "journal": _clean_str(journal),
        "publisher": _clean_str(publisher),
        "year": _clean_year(year),
        "language": _clean_lang(language),
        "country": (country or None) and str(country).strip().upper()[:2],
        "subjects": [s for s in (subjects or []) if s],
        "is_oa": is_oa,
        "peer_reviewed": peer_reviewed,
        "pdf_url": _clean_str(pdf_url),
        "landing_url": _clean_str(landing_url),
        "license": _clean_str(license),
    }
    rec["citation"] = build_citation(rec)
    return rec


def build_citation(rec: Dict[str, Any]) -> str:
    """Human-readable attribution line, e.g.

    ``Ali Veli, "Force majeure in PSAs", Revista Direito GV (2019) [DOAJ] doi:10.x/y``
    """
    bits: List[str] = []
    authors = rec.get("authors") or []
    if authors:
        bits.append(authors[0] + (" et al." if len(authors) > 2 else ""))
    if rec.get("title"):
        bits.append('"%s"' % rec["title"])
    if rec.get("journal"):
        bits.append(rec["journal"])
    if rec.get("year"):
        bits.append("(%s)" % rec["year"])
    tail = "[%s]" % rec.get("source", "?")
    if rec.get("doi"):
        tail += " doi:%s" % rec["doi"]
    elif rec.get("landing_url"):
        tail += " %s" % rec["landing_url"]
    return (", ".join(bits) + " " + tail).strip()


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _clean_doi(value: Any) -> Optional[str]:
    doi = _clean_str(value)
    if not doi:
        return None
    low = doi.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:", "https://dx.doi.org/"):
        if low.startswith(prefix):
            doi = doi[len(prefix):]
            low = doi.lower()
    return doi or None


def _clean_year(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value)
    for i in range(len(text) - 3):
        chunk = text[i:i + 4]
        if chunk.isdigit() and 1500 <= int(chunk) <= 2100:
            return int(chunk)
    return None


def _clean_lang(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    lang = _clean_str(value)
    return lang.lower()[:2] if lang else None


def dedupe(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse the same article arriving from several sources.

    Federated search double-counts constantly — a Brazilian law article is in
    SciELO, DOAJ and OpenAIRE at once. Match on DOI first (authoritative), then
    on a normalized title+year key. The surviving copy keeps the richest
    metadata and records where else it was seen in ``also_in``.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for rec in records:
        key = _dedupe_key(rec)
        if key not in merged:
            merged[key] = dict(rec)
            order.append(key)
            continue
        kept = merged[key]
        also = kept.setdefault("also_in", [])
        if rec.get("source") and rec["source"] != kept.get("source") and rec["source"] not in also:
            also.append(rec["source"])
        # Fill gaps from the duplicate; prefer a longer abstract.
        for field in FIELDS:
            if field == "source":
                continue
            new, old = rec.get(field), kept.get(field)
            if old in (None, "", []) and new not in (None, "", []):
                kept[field] = new
            elif field == "abstract" and new and old and len(str(new)) > len(str(old)):
                kept[field] = new
    return [merged[k] for k in order]


def _dedupe_key(rec: Dict[str, Any]) -> str:
    doi = rec.get("doi")
    if doi:
        return "doi:" + str(doi).lower()
    title = "".join(ch for ch in (rec.get("title") or "").lower() if ch.isalnum())
    if title:
        return "t:%s:%s" % (title[:80], rec.get("year") or "")
    return "id:%s:%s" % (rec.get("source"), rec.get("id"))
