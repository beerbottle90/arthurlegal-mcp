"""Crossref — DOI resolver and metadata backstop.

Crossref is deliberately **not** a discovery source for law here: its discipline
filter does not work (``filter=category-name:Law`` returns 0 results; subject
metadata is deprecated and largely absent). What it is good at is turning a DOI
or a precise title into authoritative metadata, and covering publishers that
open-access indexes never see.

So the router only reaches for it when a DOI is present or another source needs
its metadata completed. Uses the polite pool via ``mailto``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .._http import DEFAULT_MAILTO, get_json
from ..record import make_record

NAME = "crossref"
API = "https://api.crossref.org"

META = {
    "title": "Crossref",
    "auth": "none",
    "peer_reviewed": None,          # not asserted by Crossref
    "full_text": False,
    "languages": ["*"],
    "countries": ["*"],
    "law_filter": None,             # verified broken: category-name:Law -> 0
    "volume": "DOI universe",
    "cost": "cheap",
    "notes": "DOI/metadata resolver only — it has NO working discipline filter.",
}


def search(
    query: str = "",
    *,
    limit: int = 20,
    offset: int = 0,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    mailto: str = DEFAULT_MAILTO,
    **_: Any,
) -> Dict[str, Any]:
    """Query Crossref metadata (no discipline filter available)."""
    params: Dict[str, Any] = {
        "query": query.strip() or None,
        "rows": max(1, min(int(limit), 100)),
        "offset": max(0, int(offset)),
        "mailto": mailto,
        "select": "DOI,title,abstract,author,container-title,publisher,issued,"
                  "language,link,license,type,subject",
    }
    filters = ["type:journal-article"]
    if year_from:
        filters.append("from-pub-date:%d-01-01" % int(year_from))
    if year_to:
        filters.append("until-pub-date:%d-12-31" % int(year_to))
    params["filter"] = ",".join(filters)

    data = get_json(API, "/works", params, timeout=40)
    msg = data.get("message") or {}
    return {
        "total": msg.get("total-results"),
        "results": [_to_record(i) for i in (msg.get("items") or [])],
    }


def get(doi: str, *, mailto: str = DEFAULT_MAILTO, **_: Any) -> Dict[str, Any]:
    """Fetch authoritative metadata for a DOI."""
    data = get_json(API, "/works/" + str(doi).strip(), {"mailto": mailto}, timeout=30)
    return _to_record(data.get("message") or {})


def _to_record(item: Dict[str, Any]) -> Dict[str, Any]:
    authors: List[str] = []
    for a in item.get("author") or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name")
        if name:
            authors.append(name)

    issued = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = issued[0] if issued else None

    pdf = None
    for link in item.get("link") or []:
        if (link.get("content-type") or "").lower() == "application/pdf":
            pdf = link.get("URL")
            break

    licenses = item.get("license") or []
    return make_record(
        source=NAME,
        id=item.get("DOI"),
        doi=item.get("DOI"),
        title=(item.get("title") or [None])[0],
        abstract=item.get("abstract"),
        authors=authors,
        journal=(item.get("container-title") or [None])[0],
        publisher=item.get("publisher"),
        year=year,
        language=item.get("language"),
        subjects=item.get("subject") or [],
        is_oa=bool(licenses) or None,
        peer_reviewed=None,
        pdf_url=pdf,
        landing_url="https://doi.org/%s" % item["DOI"] if item.get("DOI") else None,
        license=(licenses[0].get("URL") if licenses else None),
    )
