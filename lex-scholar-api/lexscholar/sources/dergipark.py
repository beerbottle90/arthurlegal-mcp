"""DergiPark — Turkish academic journals, over the official OAI-PMH endpoint.

DergiPark (TÜBİTAK ULAKBİM) hosts most Turkish academic journals, including the
law faculty journals that carry Turkish legal doctrine. This adapter talks to
**DergiPark's own published OAI-PMH interface**:

    https://dergipark.org.tr/api/public/oai/

Why that matters: the widely-used third-party ``literatur-mcp`` reaches the same
content by driving a stealth browser through DergiPark's bot protection with a
paid CAPTCHA-solving service. That stack is fragile — measured 2026-07-25, its
search hung past 25s with no response while its MCP handshake still answered in
0.2s — and it is a questionable posture for a corporate legal function. The
official OAI endpoint needs no key, solves no CAPTCHA, and answered in 0.2-0.5s
in the same session. So we take the front door.

Verified live (2026-07-25):

- ``Identify`` / ``ListRecords`` / ``ListSets`` all 200, 0.2-0.5s
- metadata formats: ``oai_dc``, ``oai_etdms``, ``oai_marc``, ``oai_mods``
- records carry title, authors, **abstract** (``dc:description``), subjects,
  journal name, language and the article URL
- archive depth is real: 2015, 2020 and 2024 date windows all return records

Two upstream limits, handled here rather than hidden:

- **Rate limiting.** Roughly ten rapid requests earn a ``429``. Every call in
  this module is spaced by :data:`_DELAY`.
- **``ListSets`` stops at 100 sets** with no ``resumptionToken`` (confirmed: the
  response simply ends at ``</ListSets>``), so DergiPark's catalogue of
  thousands of journals cannot be enumerated through OAI. A ``setSpec`` does
  work when absent from that page, so coverage is built as a **verified list**
  instead — see :data:`LAW_JOURNALS`.

Coverage was assembled from three independent routes and then checked one code
at a time: the law sets DergiPark does expose, DOAJ's Turkish law-subject
journals (their homepage URL carries the DergiPark slug), and OpenAlex source
records. Scraping the site's own search was never an option — DergiPark's
``robots.txt`` disallows ``/search`` for every agent.
"""

from __future__ import annotations

import datetime as _dt
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .._http import request
from ..record import make_record

NAME = "dergipark"
OAI = "https://dergipark.org.tr/api/public/oai/"

_NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}

# Spacing between OAI calls. DergiPark returns 429 after roughly ten rapid
# requests, and a 429 costs the whole source for that query.
_DELAY = 2.0
_MAX_PAGES = 2

# Turkish law journals. **Every code below was individually confirmed live** to
# return records — this list is not guessed.
#
# It has to be curated because ``ListSets`` truncates at 100 sets with no
# resumptionToken, so the catalogue cannot be enumerated. Codes were assembled
# from three independent routes and then verified one by one: the sets DergiPark
# does expose, DOAJ's Turkish law-subject journals (their homepage URL carries
# the DergiPark slug), and OpenAlex source records.
#
# Adding one: probe ``?verb=ListRecords&metadataPrefix=oai_dc&set=<code>`` and
# read ``dc:source`` back. **Check what the journal actually is** — a plausible
# slug is not enough. ``ybhd`` looks like a law code and is the Intensive Care
# Nursing journal; ``ashd`` likewise returns medical articles. Both were caught
# this way and excluded.
LAW_JOURNALS: Dict[str, str] = {
    # Law faculty journals
    "auhfd": "Ankara Üniversitesi Hukuk Fakültesi Dergisi",
    "ahbvuhfd": "Ankara Hacı Bayram Veli Üniversitesi Hukuk Fakültesi Dergisi",
    "asbuhfd": "Ankara Sosyal Bilimler Üniversitesi Hukuk Fakültesi Dergisi",
    "andhd": "Anadolu Üniversitesi Hukuk Fakültesi Dergisi",
    "duhfd": "Dicle Üniversitesi Hukuk Fakültesi Dergisi",
    "deuhfd": "Dokuz Eylül Üniversitesi Hukuk Fakültesi Dergisi",
    "inuhfd": "İnönü Üniversitesi Hukuk Fakültesi Dergisi",
    "iuhfm": "İstanbul Üniversitesi Hukuk Fakültesi Mecmuası",
    "kouhfd": "Kocaeli Üniversitesi Hukuk Fakültesi Dergisi",
    "maruhad": "Marmara Üniversitesi Hukuk Fakültesi Hukuk Araştırmaları Dergisi",
    "neuhfd": "Necmettin Erbakan Üniversitesi Hukuk Fakültesi Dergisi",
    "shd": "Sakarya Üniversitesi Hukuk Fakültesi Dergisi",
    "suhfd": "Selçuk Üniversitesi Hukuk Fakültesi Dergisi",
    "yuhfd": "Yeditepe Üniversitesi Hukuk Fakültesi Dergisi",
    "kahd": "Karatekin Hukuk Dergisi",
    # Subject journals
    "iuchkd": "Ceza Hukuku ve Kriminoloji Dergisi",
    "iuihid": "İdare Hukuku ve İlimleri Dergisi",
    "adaletdergisi": "Adalet Dergisi",
    "ihad": "İslam Hukuku Araştırmaları Dergisi",
}

# Vocabulary that decides which journals to try first. With nineteen journals
# and a two-page-per-journal budget, picking well matters more than breadth.
_TOPIC_HINTS: Dict[str, tuple] = {
    "iuchkd": ("ceza", "kriminoloji", "suç", "suc", "criminal", "penal", "infaz"),
    "iuihid": ("idare", "idari", "administrative", "kamu", "imar", "ihale"),
    "adaletdergisi": ("yargı", "yargi", "adalet", "muhakeme", "usul", "icra", "procedure"),
    "ihad": ("islam", "fıkıh", "fikih", "şeriat", "seriat"),
    # Commercial / energy questions land in the big faculty journals.
    "maruhad": ("ticaret", "şirket", "sirket", "tahkim", "sözleşme", "sozlesme", "enerji"),
    "auhfd": ("anayasa", "vergi", "milletlerarası", "milletlerarasi", "uluslararası"),
    "iuhfm": ("deniz", "sigorta", "rekabet", "banka", "petrol", "maden"),
}

META = {
    "title": "DergiPark (Turkish academic journals)",
    "auth": "none",
    "peer_reviewed": None,          # most are hakemli, but oai_dc carries no flag
    "full_text": False,             # abstract + link to the article page
    "languages": ["tr", "en"],
    "countries": ["TR"],
    "law_filter": "19 verified law-journal setSpecs",
    "volume": "19 verified Turkish law journals",
    "cost": "medium",
    "notes": "Official OAI-PMH — no key, no CAPTCHA, unlike the third-party "
             "literatur-mcp scraper. Rate limited: calls are spaced 2s.",
}


def _fetch(query_string: str) -> str:
    """One spaced OAI call.

    The URL is assembled here rather than through ``build_url`` because that
    helper strips the trailing slash, and ``/api/public/oai?...`` answers 301
    while ``/api/public/oai/?...`` answers 200 — worth avoiding a redirect on
    every page of a harvest.
    """
    time.sleep(_DELAY)                      # 429 guard: ~10 rapid calls trip it
    return request(OAI + "?" + query_string, timeout=45).decode("utf-8", "replace")


def pick_sets(query: str, limit: int = 3) -> List[str]:
    """Choose which law journals to harvest for this question."""
    q = (query or "").lower()
    scored: List[Any] = []
    for spec, hints in _TOPIC_HINTS.items():
        hits = sum(1 for h in hints if h in q)
        if hits:
            scored.append((hits, spec))
    scored.sort(reverse=True)
    chosen = [spec for _, spec in scored]
    # Fall back to the largest general faculty journals, which carry the widest
    # range of doctrine when the question matches no subject vocabulary.
    for spec in ("maruhad", "auhfd", "iuhfm", "deuhfd", "suhfd", "shd"):
        if spec not in chosen:
            chosen.append(spec)
    return chosen[:limit]


def search(
    query: str = "",
    *,
    limit: int = 20,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    sets: Optional[List[str]] = None,
    **_: Any,
) -> Dict[str, Any]:
    """Harvest the chosen law journals and match client-side.

    OAI-PMH has no free-text search, so the journal set is the filter and the
    query is applied to the harvested window — the same shape as the Dialnet and
    Law Review Commons adapters.
    """
    chosen = sets or pick_sets(query)
    terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]

    out: List[Dict[str, Any]] = []
    scanned = 0
    for spec in chosen:
        token: Optional[str] = None
        for _page in range(_MAX_PAGES):
            if token:
                q = "verb=ListRecords&resumptionToken=" + urllib.parse.quote(token)
            else:
                q = "verb=ListRecords&metadataPrefix=oai_dc&set=" + urllib.parse.quote(spec)
                if year_from:
                    q += "&from=%d-01-01" % int(year_from)
                if year_to:
                    q += "&until=%d-12-31" % int(year_to)
            try:
                root = ET.fromstring(_fetch(q))
            except Exception:
                break
            if root.find(".//{%s}error" % _NS["oai"]) is not None:
                break
            for node in root.iter("{%s}record" % _NS["oai"]):
                scanned += 1
                rec = _to_record(node)
                if rec and _matches(rec, terms, year_from, year_to):
                    out.append(rec)
                    if len(out) >= limit:
                        return {"total": None, "scanned": scanned, "sets": chosen,
                                "results": out}
            tok = root.find(".//{%s}resumptionToken" % _NS["oai"])
            token = (tok.text or "").strip() if tok is not None and tok.text else None
            if not token:
                break
    return {"total": None, "scanned": scanned, "sets": chosen, "results": out}


def list_journals() -> List[Dict[str, str]]:
    """The curated law journals this adapter can address."""
    return [{"setSpec": k, "name": v} for k, v in LAW_JOURNALS.items()]


def _matches(rec: Dict[str, Any], terms: List[str],
             year_from: Optional[int], year_to: Optional[int]) -> bool:
    year = rec.get("year")
    if year_from and (year or 0) < year_from:
        return False
    if year_to and (year or 9999) > year_to:
        return False
    if not terms:
        return True
    hay = " ".join(str(rec.get(f) or "") for f in
                   ("title", "abstract", "journal", "subjects")).lower()
    hits = sum(1 for t in terms if t in hay)
    needed = len(terms) if len(terms) <= 2 else (len(terms) + 1) // 2
    return hits >= needed


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
    ids = _all(node, "identifier")
    landing = next((u for u in ids if u.startswith("http")), None)
    pdf = next((u for u in ids if u.lower().endswith(".pdf")), None)
    header = node.find("{%s}header" % _NS["oai"])
    oai_id = header.findtext("{%s}identifier" % _NS["oai"], "") if header is not None else ""

    return make_record(
        source=NAME,
        id=oai_id.rsplit("/", 1)[-1] or landing,
        title=title,
        abstract=_first(node, "description"),
        authors=_all(node, "creator"),
        journal=_first(node, "source"),
        publisher=_first(node, "publisher"),
        year=_first(node, "date"),
        language=_first(node, "language") or "tr",
        country="TR",
        subjects=_all(node, "subject")[:5],
        is_oa=True,                 # DergiPark is an open-access platform
        peer_reviewed=None,         # most journals are hakemli; oai_dc says nothing
        pdf_url=pdf,
        landing_url=landing,
        license=_first(node, "rights"),
    )
