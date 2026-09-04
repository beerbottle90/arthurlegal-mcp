"""Law Review Commons — 351,887 US law-review articles over OAI-PMH, no auth.

This is the highest-value source for energy and resources law, because its 67 sets are
organised **by subject**, and four of them map straight onto the practice:

    oil-gas-mineral-law              845
    energy-utilities-law           1,305
    dispute-resolution-arbitration 2,082
    natural-resources-law          8,738

That matters because no dedicated open-access *energy law* journal exists —
energy scholarship is scattered through general law journals, so topical sets
beat journal-level filtering.

Two honest caveats, both verified:

- US law reviews are **student-edited, not blind peer-reviewed**, so records are
  flagged ``peer_reviewed=False``. They are authoritative practice literature,
  not peer-reviewed scholarship; do not mix the two silently.
- OAI-PMH has **no free-text search**. We therefore use the subject set as the
  filter and match query terms client-side over the harvested page window.
- the ``viewcontent`` PDF link answers **HTTP 202 with an empty body** behind a
  bepress bot interstitial, so ``pdf_url`` is offered as a link but full text is
  not promised. ``robots.txt`` disallows ``/do/`` for generic agents.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .._http import get_text
from ..record import make_record

NAME = "lawreviewcommons"
OAI = "https://lawreviewcommons.com/do/oai/"

_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}

# Subject sets that matter for an energy/resources legal practice, with the
# record counts verified live. Order is the router's preference order.
SETS: Dict[str, Dict[str, Any]] = {
    "oil-gas-mineral-law": {"count": 845, "terms": [
        "oil", "gas", "petroleum", "mineral", "hydrocarbon", "upstream",
        "psa", "production sharing", "drilling", "opec", "lng"]},
    "energy-utilities-law": {"count": 1305, "terms": [
        "energy", "utility", "utilities", "power", "electricity", "grid",
        "renewable", "nuclear", "pipeline", "tariff"]},
    "dispute-resolution-arbitration": {"count": 2082, "terms": [
        "arbitration", "arbitral", "dispute", "icsid", "uncitral", "icc",
        "mediation", "award", "tribunal", "investor-state", "bit"]},
    "natural-resources-law": {"count": 8738, "terms": [
        "natural resource", "mining", "environment", "environmental",
        "water", "land", "concession", "extractive", "climate"]},
    "international-trade-law": {"count": 2281, "terms": [
        "trade", "wto", "sanction", "export", "import", "customs", "tariff"]},
}

_PAGE = 100          # OAI page size is fixed by the server
# Each OAI page is a separate round trip, so the window is kept tight; the
# subject set is already the real filter. ``scanned`` reports what was covered.
_MAX_PAGES = 2


def pick_sets(query: str, limit: int = 2) -> List[str]:
    """Score the subject sets against the query and return the best matches."""
    q = (query or "").lower()
    scored: List[Any] = []
    for name, meta in SETS.items():
        hits = sum(1 for t in meta["terms"] if t in q)
        if hits:
            scored.append((hits, -meta["count"], name))
    if not scored:
        return ["oil-gas-mineral-law", "energy-utilities-law"][:limit]
    scored.sort(reverse=True)
    return [name for _, _, name in scored[:limit]]


META = {
    "title": "Law Review Commons (US law reviews)",
    "auth": "none",
    "peer_reviewed": False,     # student-edited law reviews
    "full_text": "partial",     # PDF link present; bepress interstitial may block
    "languages": ["en"],
    "countries": ["US"],
    "law_filter": "67 OAI subject sets (oil-gas, energy, arbitration, ...)",
    "volume": "351,887 records",
    "cost": "medium",           # OAI paging, no server-side search
    "notes": "Student-edited, NOT peer reviewed. Best topical coverage of "
             "oil & gas / energy / arbitration scholarship anywhere.",
}


def search(
    query: str = "",
    *,
    limit: int = 20,
    sets: Optional[List[str]] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    **_: Any,
) -> Dict[str, Any]:
    """Harvest the best-matching subject set(s) and filter client-side."""
    chosen = sets or pick_sets(query)
    terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]

    out: List[Dict[str, Any]] = []
    scanned = 0
    for set_name in chosen:
        token: Optional[str] = None
        for _page in range(_MAX_PAGES):
            params = ({"verb": "ListRecords", "resumptionToken": token} if token else
                      {"verb": "ListRecords", "metadataPrefix": "oai_dc",
                       "set": "publication:" + set_name})
            try:
                xml = get_text(OAI, "", params, timeout=40)
            except Exception:
                break
            root = ET.fromstring(xml)
            for node in root.iter("{%s}record" % _NS["oai"]):
                scanned += 1
                rec = _to_record(node, set_name)
                if rec and _matches(rec, terms, year_from, year_to):
                    out.append(rec)
                    if len(out) >= limit:
                        return {"total": None, "scanned": scanned,
                                "sets": chosen, "results": out}
            tok = root.find(".//{%s}resumptionToken" % _NS["oai"])
            token = (tok.text or "").strip() if tok is not None else None
            if not token:
                break
    return {"total": None, "scanned": scanned, "sets": chosen, "results": out}


def _matches(rec: Dict[str, Any], terms: List[str],
             year_from: Optional[int], year_to: Optional[int]) -> bool:
    year = rec.get("year")
    if year_from and (year or 0) < year_from:
        return False
    if year_to and (year or 9999) > year_to:
        return False
    if not terms:
        return True
    hay = " ".join(str(rec.get(f) or "") for f in ("title", "abstract", "journal")).lower()
    return any(t in hay for t in terms)


def _first(node: ET.Element, tag: str) -> Optional[str]:
    el = node.find(".//{%s}%s" % (_NS["dc"], tag))
    return (el.text or "").strip() if el is not None and el.text else None


def _all(node: ET.Element, tag: str) -> List[str]:
    return [(e.text or "").strip() for e in node.findall(".//{%s}%s" % (_NS["dc"], tag))
            if e is not None and e.text]


def _to_record(node: ET.Element, set_name: str) -> Optional[Dict[str, Any]]:
    title = _first(node, "title")
    if not title:
        return None
    ids = _all(node, "identifier")
    pdf = next((u for u in ids if u.startswith("http") and
                ("viewcontent" in u or u.endswith(".pdf"))), None)
    landing = next((u for u in ids if u.startswith("http")), None)
    header_id = node.find(".//{%s}identifier" % _NS["oai"])

    return make_record(
        source=NAME,
        id=(header_id.text or "").strip() if header_id is not None and header_id.text else landing,
        title=title,
        abstract=_first(node, "description"),
        authors=_all(node, "creator"),
        journal=_first(node, "source"),
        publisher=_first(node, "publisher"),
        year=_first(node, "date"),
        language="en",
        country="US",
        subjects=[set_name] + _all(node, "subject")[:5],
        is_oa=True,
        peer_reviewed=False,        # student-edited law reviews
        pdf_url=pdf,
        landing_url=landing,
        license=_first(node, "rights"),
    )
