"""Unpaywall — DOI → best legally-free copy.

Not a search engine: it answers exactly one question, "is there an open copy of
this DOI, and where?". That makes it the federation's OA-resolution layer — any
hit from Crossref, DOAJ or OpenAIRE that lacks a PDF gets upgraded here.

Auth is an ``email`` query parameter, not a key or registration, so it stays
within the no-auth remit. 100k calls/day.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .._http import DEFAULT_MAILTO, get_json
from ..record import make_record

NAME = "unpaywall"
API = "https://api.unpaywall.org/v2"

META = {
    "title": "Unpaywall",
    "auth": "none (email parameter)",
    "peer_reviewed": None,
    "full_text": "locator",         # finds the PDF, does not host it
    "languages": ["*"],
    "countries": ["*"],
    "law_filter": None,
    "volume": "DOI universe",
    "cost": "cheap",
    "notes": "DOI -> open-access copy. Use to upgrade hits that lack a PDF.",
}


def get(doi: str, *, email: str = DEFAULT_MAILTO, **_: Any) -> Dict[str, Any]:
    """Resolve one DOI to its best open-access location."""
    data = get_json(API, "/" + str(doi).strip(), {"email": email}, timeout=30)
    return _to_record(data)


def best_oa_url(doi: str, *, email: str = DEFAULT_MAILTO) -> Optional[str]:
    """Return just the best OA PDF/landing URL for a DOI, or None."""
    try:
        rec = get(doi, email=email)
    except Exception:
        return None
    return rec.get("pdf_url") or rec.get("landing_url")


def search(query: str = "", **_: Any) -> Dict[str, Any]:
    """Unpaywall has no discovery endpoint; kept for a uniform adapter surface."""
    return {"total": 0, "results": [],
            "note": "unpaywall resolves DOIs; it does not support free-text search"}


def _to_record(data: Dict[str, Any]) -> Dict[str, Any]:
    best = data.get("best_oa_location") or {}
    return make_record(
        source=NAME,
        id=data.get("doi"),
        doi=data.get("doi"),
        title=data.get("title"),
        authors=[
            " ".join(x for x in (a.get("given"), a.get("family")) if x)
            for a in (data.get("z_authors") or []) if isinstance(a, dict)
        ],
        journal=data.get("journal_name"),
        publisher=data.get("publisher"),
        year=data.get("year"),
        is_oa=data.get("is_oa"),
        peer_reviewed=None,
        pdf_url=best.get("url_for_pdf"),
        landing_url=best.get("url_for_landing_page") or data.get("doi_url"),
        license=best.get("license"),
    )
