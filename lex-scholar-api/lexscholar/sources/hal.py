"""HAL — French national open archive. Best francophone legal doctrine.

HAL has the cleanest discipline facet of any source tested:
``domainAllCode_s:shs.droit`` → **236,486 law documents, 26,422 open access**.
It also carries an explicit ``peerReviewing_s`` flag, so peer-review status is
read from the record instead of being inferred.

Gotcha handled here: the intuitive ``domain_s`` field silently returns 0 —
``domainAllCode_s`` is the one that works. Full text is a real PDF via
``fileMain_s``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .._http import get_json
from ..record import make_record

NAME = "hal"
API = "https://api.archives-ouvertes.fr/search/"

LAW_DOMAIN = "domainAllCode_s:shs.droit"

_FIELDS = ",".join([
    "docid", "title_s", "abstract_s", "authFullName_s", "journalTitle_s",
    "producedDateY_i", "language_s", "doiId_s", "uri_s", "fileMain_s",
    "openAccess_bool", "peerReviewing_s", "publisher_s",
])

META = {
    "title": "HAL (France)",
    "auth": "none",
    "peer_reviewed": "flagged",     # peerReviewing_s on each record
    "full_text": True,              # fileMain_s PDF + TEI
    "languages": ["fr", "en"],
    "countries": ["FR"],
    "law_filter": LAW_DOMAIN,
    "volume": "236,486 law docs / 26,422 OA",
    "cost": "cheap",
    "notes": "Use domainAllCode_s — the intuitive domain_s silently returns 0.",
}


def search(
    query: str = "",
    *,
    limit: int = 20,
    offset: int = 0,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    open_access_only: bool = False,
    peer_reviewed_only: bool = False,
    **_: Any,
) -> Dict[str, Any]:
    """Search French legal scholarship."""
    fq: List[str] = [LAW_DOMAIN]
    if open_access_only:
        fq.append("openAccess_bool:true")
    if peer_reviewed_only:
        fq.append("peerReviewing_s:1")
    if year_from or year_to:
        fq.append("producedDateY_i:[%s TO %s]" % (year_from or "*", year_to or "*"))

    q = query.strip()
    params: Dict[str, Any] = {
        "q": q or "*:*",
        "fq": fq,
        "rows": max(1, min(int(limit), 100)),
        "start": max(0, int(offset)),
        "wt": "json",
        "fl": _FIELDS,
    }
    # Only sort by date when browsing. With a real query, forcing a date sort
    # discards Solr's relevance ranking and returns whatever is newest in the
    # law domain — which looked like the query had been ignored entirely.
    if not q:
        params["sort"] = "producedDateY_i desc"

    data = get_json(API, "", params, timeout=40)
    resp = data.get("response") or {}
    return {
        "total": resp.get("numFound"),
        "results": [_to_record(d) for d in (resp.get("docs") or [])],
    }


def get(docid: str, **_: Any) -> Dict[str, Any]:
    """Fetch one HAL document by docid."""
    data = get_json(API, "", {"q": "docid:%s" % docid, "wt": "json", "fl": _FIELDS, "rows": 1},
                    timeout=30)
    docs = (data.get("response") or {}).get("docs") or []
    if not docs:
        raise KeyError("HAL docid not found: %s" % docid)
    return _to_record(docs[0])


def _one(value: Any) -> Optional[str]:
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value is not None else None


def _to_record(doc: Dict[str, Any]) -> Dict[str, Any]:
    peer = doc.get("peerReviewing_s")
    peer_flag: Optional[bool]
    if peer is None:
        peer_flag = None
    else:
        peer_flag = str(_one(peer)).lower() in ("1", "true", "yes")

    return make_record(
        source=NAME,
        id=_one(doc.get("docid")),
        doi=_one(doc.get("doiId_s")),
        title=_one(doc.get("title_s")),
        abstract=_one(doc.get("abstract_s")),
        authors=[a for a in (doc.get("authFullName_s") or []) if a],
        journal=_one(doc.get("journalTitle_s")),
        publisher=_one(doc.get("publisher_s")),
        year=_one(doc.get("producedDateY_i")),
        language=_one(doc.get("language_s")),
        country="FR",
        subjects=["shs.droit"],
        is_oa=bool(doc.get("openAccess_bool")),
        peer_reviewed=peer_flag,
        pdf_url=_one(doc.get("fileMain_s")),
        landing_url=_one(doc.get("uri_s")),
    )
