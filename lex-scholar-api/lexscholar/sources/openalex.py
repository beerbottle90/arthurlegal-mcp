"""OpenAlex — widest law coverage, but metered. Held in reserve by the router.

Coverage is unmatched: subfield **3308 = Law** holds 2,244,618 works, 895,966 of
them journal articles, 362,231 open access. It is also the only way to reach
jurisdictions that have no open-access law journal at all — Azerbaijan has
**zero** DOAJ law journals but **193 OA law works** here.

The catch, verified live rather than taken from docs: since February 2026
OpenAlex is **metered**. Anonymous calls still return 200, but the response
carries ``X-RateLimit-Limit: 1000`` with ``X-RateLimit-Limit-USD: 0.1``/day, and
a list request costs 10 credits — roughly **100 anonymous searches a day**. A
free key raises that ~100x.

So this module:

- sends ``api_key`` when ``OPENALEX_API_KEY`` is set,
- reads the budget back off the response headers, and
- exposes :func:`budget_low` so the router can skip it *before* it 429s,
  instead of burning the last credits on a query DOAJ could have answered.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .._http import DEFAULT_MAILTO, budget_for, get_json
from .._text import abstract_from_inverted_index
from ..record import make_record

NAME = "openalex"
API = "https://api.openalex.org"
_HOST = "api.openalex.org"

LAW_SUBFIELD = "primary_topic.subfield.id:subfields/3308"

# Below this many remaining credits, the router should leave OpenAlex alone.
LOW_WATER_MARK = 60

META = {
    "title": "OpenAlex",
    "auth": "optional key (metered since Feb 2026)",
    "peer_reviewed": "inferred",    # via type:article + journal venue
    "full_text": False,             # OA locations link out; PDFs often absent
    "languages": ["*"],
    "countries": ["*"],
    "law_filter": LAW_SUBFIELD,
    "volume": "2,244,618 law works / 362,231 OA",
    "cost": "metered",
    "notes": "Anonymous ~100 searches/day. Set OPENALEX_API_KEY for ~100x more. "
             "Reserve for gap jurisdictions (e.g. AZ) and citation traversal.",
}


def api_key() -> Optional[str]:
    return os.environ.get("OPENALEX_API_KEY") or None


def budget_low() -> bool:
    """True when the last-seen anonymous budget is nearly exhausted."""
    if api_key():
        return False
    remaining = budget_for(_HOST).get("remaining")
    return remaining is not None and remaining < LOW_WATER_MARK


def budget() -> Dict[str, Any]:
    """Expose the last-seen rate-limit budget (for list_sources)."""
    info = budget_for(_HOST)
    info["has_key"] = bool(api_key())
    return info


def search(
    query: str = "",
    *,
    limit: int = 20,
    offset: int = 0,
    country: Optional[str] = None,
    language: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    open_access_only: bool = False,
    peer_reviewed_only: bool = False,
    **_: Any,
) -> Dict[str, Any]:
    """Search law works, optionally scoped to a country's authors."""
    filters: List[str] = [LAW_SUBFIELD]
    if peer_reviewed_only:
        # OpenAlex has no peer-review flag; a journal article is the best proxy.
        filters += ["type:article", "primary_location.source.type:journal"]
    if open_access_only:
        filters.append("is_oa:true")
    if country:
        filters.append("authorships.countries:%s" % country.upper())
    if language:
        filters.append("language:%s" % language.lower())
    if year_from:
        filters.append("from_publication_date:%d-01-01" % int(year_from))
    if year_to:
        filters.append("to_publication_date:%d-12-31" % int(year_to))

    size = max(1, min(int(limit), 200))
    params: Dict[str, Any] = {
        "filter": ",".join(filters),
        "per-page": size,
        "page": (max(0, int(offset)) // size) + 1,
        "mailto": DEFAULT_MAILTO,
    }
    if query and query.strip():
        params["search"] = query.strip()
    key = api_key()
    if key:
        params["api_key"] = key

    data = get_json(API, "/works", params, timeout=40)
    return {
        "total": (data.get("meta") or {}).get("count"),
        "budget": budget(),
        "results": [_to_record(w) for w in (data.get("results") or [])],
    }


def get(work_id: str, **_: Any) -> Dict[str, Any]:
    """Fetch one work by OpenAlex id or DOI."""
    ident = str(work_id).strip()
    if ident.lower().startswith("10."):
        ident = "https://doi.org/" + ident
    params: Dict[str, Any] = {"mailto": DEFAULT_MAILTO}
    key = api_key()
    if key:
        params["api_key"] = key
    return _to_record(get_json(API, "/works/" + ident, params, timeout=30))


def _to_record(work: Dict[str, Any]) -> Dict[str, Any]:
    best = work.get("best_oa_location") or work.get("primary_location") or {}
    source = best.get("source") or {}
    venue_type = (source.get("type") or "").lower()
    work_type = (work.get("type") or "").lower()

    return make_record(
        source=NAME,
        id=(work.get("id") or "").rsplit("/", 1)[-1] or None,
        doi=work.get("doi"),
        title=work.get("title") or work.get("display_name"),
        abstract=abstract_from_inverted_index(work.get("abstract_inverted_index")),
        authors=[(a.get("author") or {}).get("display_name")
                 for a in (work.get("authorships") or [])
                 if (a.get("author") or {}).get("display_name")],
        journal=source.get("display_name"),
        publisher=source.get("host_organization_name"),
        year=work.get("publication_year"),
        language=work.get("language"),
        country=_first_country(work),
        subjects=[(work.get("primary_topic") or {}).get("display_name")]
        if work.get("primary_topic") else [],
        is_oa=work.get("is_oa") if work.get("is_oa") is not None else best.get("is_oa"),
        # Journal article in a journal venue is the closest proxy OpenAlex offers.
        peer_reviewed=True if (work_type == "article" and venue_type == "journal") else None,
        pdf_url=best.get("pdf_url"),
        landing_url=best.get("landing_page_url") or work.get("doi"),
        license=best.get("license"),
    )


def _first_country(work: Dict[str, Any]) -> Optional[str]:
    for a in work.get("authorships") or []:
        for c in a.get("countries") or []:
            if c:
                return c
    return None
