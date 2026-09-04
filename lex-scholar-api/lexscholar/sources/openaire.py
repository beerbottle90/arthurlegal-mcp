"""OpenAIRE Graph — pan-European aggregator over national repositories.

Two things make it worth a slot: an explicit ``isPeerReviewed`` signal, and a
Fields-of-Science filter (``0505`` = Law) giving **582,820** law records. It
reaches EU-funded and "diamond" open-access output that DOAJ and OpenAlex miss,
which matters for EU energy-law and Energy Charter Treaty scholarship.

The Graph v1 payload is loosely typed and its field names have shifted between
API generations, so every accessor here is defensive: unexpected shapes degrade
to ``None`` rather than raising.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .._http import get_json
from ..record import make_record

NAME = "openaire"
API = "https://api.openaire.eu/graph/v1"

FOS_LAW = "0505 law"

META = {
    "title": "OpenAIRE Graph (EU)",
    "auth": "none",
    "peer_reviewed": "flagged",     # isPeerReviewed on the record
    "full_text": False,             # link-out to the hosting repository
    "languages": ["*"],
    "countries": ["EU"],
    "law_filter": "fos=0505 law",
    "volume": "582,820 law records",
    "cost": "cheap",
    "notes": "Reaches EU-funded and diamond-OA output other indexes miss.",
}


def search(
    query: str = "",
    *,
    limit: int = 20,
    offset: int = 0,
    country: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    peer_reviewed_only: bool = False,
    **_: Any,
) -> Dict[str, Any]:
    """Search EU research products filtered to Law (and optionally a country)."""
    size = max(1, min(int(limit), 100))
    params: Dict[str, Any] = {
        "search": query.strip() or None,
        "fos": FOS_LAW,
        "type": "publication",
        "pageSize": size,
        "page": (max(0, int(offset)) // size) + 1,
    }
    # Verified: `countryCode` is the parameter that works (AZ narrows 44,275 to
    # 4). `country` / `relCountryCode` break the response instead of filtering.
    if country:
        params["countryCode"] = str(country).strip().upper()[:2]
    if year_from:
        params["fromPublicationDate"] = "%d-01-01" % int(year_from)
    if year_to:
        params["toPublicationDate"] = "%d-12-31" % int(year_to)
    if peer_reviewed_only:
        params["isPeerReviewed"] = "true"

    data = get_json(API, "/researchProducts", params, timeout=45)
    header = data.get("header") or {}
    results = data.get("results") or []
    return {
        "total": header.get("numFound") or header.get("total"),
        "results": [_to_record(r) for r in results],
    }


def get(product_id: str, **_: Any) -> Dict[str, Any]:
    """Fetch one research product by OpenAIRE id."""
    data = get_json(API, "/researchProducts", {"id": product_id, "pageSize": 1}, timeout=30)
    results = data.get("results") or []
    if not results:
        raise KeyError("OpenAIRE id not found: %s" % product_id)
    return _to_record(results[0])


def _text(value: Any) -> Optional[str]:
    """Unwrap the several shapes OpenAIRE uses for a string field."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("value", "content", "name", "fullName"):
            if value.get(key):
                return str(value[key])
        return None
    if isinstance(value, list):
        for item in value:
            got = _text(item)
            if got:
                return got
    return None


def _to_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    authors: List[str] = []
    for a in raw.get("authors") or []:
        name = _text(a)
        if name:
            authors.append(name)

    doi = None
    for pid in raw.get("pids") or []:
        scheme = (_text(pid.get("scheme")) or "").lower() if isinstance(pid, dict) else ""
        if scheme == "doi":
            doi = _text(pid.get("value"))
            break

    instances = raw.get("instances") or []
    landing = pdf = license_ = None
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        for url in inst.get("urls") or []:
            if isinstance(url, str):
                landing = landing or url
                if url.lower().endswith(".pdf"):
                    pdf = pdf or url
        license_ = license_ or _text(inst.get("license"))

    peer = raw.get("isPeerReviewed")
    if isinstance(peer, str):
        peer = peer.lower() == "true"

    container = raw.get("container") or {}
    return make_record(
        source=NAME,
        id=_text(raw.get("id")),
        doi=doi,
        title=_text(raw.get("mainTitle")) or _text(raw.get("title")),
        abstract=_text(raw.get("descriptions")) or _text(raw.get("description")),
        authors=authors,
        journal=_text(container.get("name")) if isinstance(container, dict) else None,
        publisher=_text(raw.get("publisher")),
        year=_text(raw.get("publicationDate")),
        language=_text(raw.get("language")),
        subjects=["law"],
        is_oa=(_text(raw.get("bestAccessRight")) or "").lower().startswith("open") or None,
        peer_reviewed=peer if isinstance(peer, bool) else None,
        pdf_url=pdf,
        landing_url=landing,
        license=license_,
    )
