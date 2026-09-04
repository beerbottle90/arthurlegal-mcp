"""Dialnet — the largest Spanish-language legal corpus (discovery only).

``set=18`` is a genuine native "Ciencias jurídicas" facet — the best law
filterability of any source here — covering a measured **95,402 law records**,
growing ~600/month.

Deliberately limited to discovery, for two reasons found during verification:

- records carry a landing page (``servlet/oaiart``) and **no PDF** — 100/100
  sampled records had no file link;
- ``dc:rights`` is **restrictive**: it requires express written consent for
  reproduction. So this adapter surfaces citations and never promises text, and
  ``license`` is passed through verbatim so the obligation travels with the record.

OAI-PMH also has no free-text search, so queries are served by harvesting a
recent date window and matching client-side. That is bounded and honest: the
response reports how much was scanned.
"""

from __future__ import annotations

import datetime as _dt
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .._http import get_text
from ..record import make_record

NAME = "dialnet"
OAI = "https://dialnet.unirioja.es/oai/OAIHandler"

LAW_SET = "18"  # Ciencias jurídicas

_NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}
_MAX_PAGES = 3

META = {
    "title": "Dialnet (Spain)",
    "auth": "none",
    "peer_reviewed": None,          # mixed; Dialnet indexes journals and chapters
    "full_text": False,             # landing page only — verified
    "languages": ["es"],
    "countries": ["ES"],
    "law_filter": "set=18 (Ciencias jurídicas)",
    "volume": "95,402 law records",
    "cost": "medium",
    "notes": "DISCOVERY ONLY. dc:rights requires express written consent for "
             "reproduction — cite, never redistribute.",
}


def search(
    query: str = "",
    *,
    limit: int = 20,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    window_days: int = 120,
    **_: Any,
) -> Dict[str, Any]:
    """Harvest a recent slice of the law set and match client-side."""
    until = _dt.date.today()
    if year_to:
        until = min(until, _dt.date(int(year_to), 12, 31))
    since = until - _dt.timedelta(days=max(7, int(window_days)))
    if year_from:
        since = max(since, _dt.date(int(year_from), 1, 1))

    terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]
    out: List[Dict[str, Any]] = []
    scanned = 0
    token: Optional[str] = None

    for _page in range(_MAX_PAGES):
        params = ({"verb": "ListRecords", "resumptionToken": token} if token else
                  {"verb": "ListRecords", "metadataPrefix": "oai_dc", "set": LAW_SET,
                   "from": since.isoformat(), "until": until.isoformat()})
        try:
            xml = get_text(OAI, "", params, timeout=60)
        except Exception:
            break
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            break
        for node in root.iter("{%s}record" % _NS["oai"]):
            scanned += 1
            rec = _to_record(node)
            if rec and _matches(rec, terms):
                out.append(rec)
                if len(out) >= limit:
                    return {"total": None, "scanned": scanned,
                            "window": [since.isoformat(), until.isoformat()], "results": out}
        tok = root.find(".//{%s}resumptionToken" % _NS["oai"])
        token = (tok.text or "").strip() if tok is not None and tok.text else None
        if not token:
            break

    return {"total": None, "scanned": scanned,
            "window": [since.isoformat(), until.isoformat()], "results": out}


def _matches(rec: Dict[str, Any], terms: List[str]) -> bool:
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


def _to_record(node: ET.Element) -> Optional[Dict[str, Any]]:
    title = _first(node, "title")
    if not title:
        return None
    landing = next((u for u in _all(node, "identifier") if u.startswith("http")), None)
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
        language=_first(node, "language") or "es",
        country="ES",
        subjects=["ciencias juridicas"],
        is_oa=None,
        peer_reviewed=None,
        pdf_url=None,               # verified: Dialnet exposes no file links
        landing_url=landing,
        license=_first(node, "rights"),
    )
