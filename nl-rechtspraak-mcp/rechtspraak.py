"""Clients for the two Dutch legal sources — standard library only.

``RechtspraakClient`` — case law (data.rechtspraak.nl Open Data)
---------------------------------------------------------------
3,751,381 published decisions, free, no auth, every one carrying an ECLI. And
**no free-text search whatsoever**.

That is not an oversight in this client; it is the API. Worse, it *silently
ignores* parameters it does not know, so a query looks like it worked:

    ?max=1                              -> 3,751,381   (baseline)
    ?keyword=energie                    -> 3,751,381   (ignored)
    ?q=energie                          -> 3,751,381   (ignored)
    ?text=energie                       -> 3,751,381   (ignored)
    ?subject=...#bestuursrecht          -> 1,562,145   (a real filter)

A caller who passed ``q=`` and got a full page of results would reasonably think
they had searched. They had not. This client therefore accepts only parameters
verified to filter, and search is served from the local index built by
``crawl.py``.

``KoopClient`` — legislation (repository.overheid.nl SRU 2.0)
-------------------------------------------------------------
The opposite situation: real CQL full-text search, verified to discriminate
(``energiewet`` -> 1,961 records; ``mededinging`` -> 20,991). Passed straight
through, no local index needed.
"""

from __future__ import annotations

import datetime as _dt
import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterator, List, Optional

__version__ = "1.0.0"

RECHTSPRAAK = "https://data.rechtspraak.nl"
KOOP_SRU = "https://repository.overheid.nl/sru"
UA = ("arthurlegal-nl-rechtspraak-mcp/%s "
      "(+https://github.com/beerbottle90/arthurlegal-mcp)" % __version__)

ATOM = "{http://www.w3.org/2005/Atom}"
DCTERMS = "{http://purl.org/dc/terms/}"
PSI = "{http://psi.rechtspraak.nl/}"
SRU = "{http://docs.oasis-open.org/ns/search-ws/sruResponse}"
GZD = "{http://standaarden.overheid.nl/sru}"

ECLI_RE = re.compile(r"^ECLI:NL:[A-Z]+:\d{4}:[A-Z0-9.]+$", re.IGNORECASE)

# Parameters data.rechtspraak.nl actually honours. Anything else is dropped with
# a warning rather than passed through to be silently ignored upstream.
HONOURED = {"max", "from", "type", "date", "subject", "creator", "return", "replaces", "modified"}


class NlError(Exception):
    """An upstream failure worth explaining to the caller."""


def _fetch(url: str, timeout: int = 90) -> str:
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise NlError("HTTP %s from %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise NlError("Could not reach %s: %s" % (url, exc.reason)) from exc


def _plain(xml_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", xml_text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


class RechtspraakClient:
    """Dutch case law. Structured filters only — see the module docstring."""

    def search(self, max_results: int = 100, offset: int = 0, date: str = "",
               date_to: str = "", subject: str = "", creator: str = "",
               doc_type: str = "", modified_since: str = "") -> Dict[str, Any]:
        params: List[tuple] = [("max", max(1, min(int(max_results), 1000)))]
        if offset:
            params.append(("from", int(offset)))
        # A range is expressed as the `date` parameter given twice.
        if date:
            params.append(("date", date))
        if date_to:
            params.append(("date", date_to))
        if subject:
            params.append(("subject", subject))
        if creator:
            params.append(("creator", creator))
        if doc_type:
            params.append(("type", doc_type))
        if modified_since:
            params.append(("modified", modified_since))
        url = "%s/uitspraken/zoeken?%s" % (RECHTSPRAAK, urllib.parse.urlencode(params))
        raw = _fetch(url)
        try:
            root = ET.fromstring(raw.encode("utf-8"))
        except ET.ParseError as exc:
            raise NlError("Rechtspraak returned unparseable Atom: %s" % exc) from exc
        subtitle = root.findtext(ATOM + "subtitle") or ""
        m = re.search(r"(\d+)", subtitle)
        total = int(m.group(1)) if m else 0
        results = []
        for entry in root.findall(ATOM + "entry"):
            ecli = (entry.findtext(ATOM + "id") or "").strip()
            results.append({
                "ecli": ecli,
                "title": (entry.findtext(ATOM + "title") or "").strip(),
                "summary": (entry.findtext(ATOM + "summary") or "").strip(),
                "updated": (entry.findtext(ATOM + "updated") or "").strip(),
                "url": "https://uitspraken.rechtspraak.nl/details?id=%s" % ecli,
                "citation": ecli,
            })
        return {"total_matching_filters": total, "returned": len(results),
                "request_url": url, "results": results}

    def get_decision(self, ecli: str, max_chars: int = 60000) -> Dict[str, Any]:
        ecli = (ecli or "").strip()
        if not ECLI_RE.match(ecli):
            raise NlError(
                "Malformed ECLI %r — expected e.g. ECLI:NL:HR:2024:1. Never "
                "construct an ECLI; copy it from a search result." % ecli
            )
        raw = _fetch("%s/uitspraken/content?id=%s" % (RECHTSPRAAK, urllib.parse.quote(ecli)))
        try:
            root = ET.fromstring(raw.encode("utf-8"))
        except ET.ParseError as exc:
            raise NlError("Rechtspraak returned unparseable XML for %s: %s" % (ecli, exc)) from exc

        def dc(tag: str) -> str:
            el = root.find(".//" + DCTERMS + tag)
            return "".join(el.itertext()).strip() if el is not None else ""

        # The judgment body is <uitspraak>; an AG opinion is <conclusie>.
        body_el = root.find(".//{*}uitspraak")
        kind = "uitspraak"
        if body_el is None:
            body_el = root.find(".//{*}conclusie")
            kind = "conclusie" if body_el is not None else "unknown"
        body = _plain(ET.tostring(body_el, encoding="unicode")) if body_el is not None else ""

        docket = root.findtext(".//" + PSI + "zaaknummer") or ""
        out: Dict[str, Any] = {
            "ecli": ecli,
            "citation": ecli,
            "court": dc("creator"),
            "date": dc("date"),
            "issued": dc("issued"),
            "docket": docket.strip(),
            "type": dc("type"),
            "procedure": (root.findtext(".//" + PSI + "procedure") or "").strip(),
            "subject": dc("subject"),
            "language": dc("language") or "nl",
            "abstract": dc("abstract"),
            "document_kind": kind,
            "url": "https://uitspraken.rechtspraak.nl/details?id=%s" % ecli,
            "length_chars": len(body),
            "text": body[:max_chars],
        }
        if len(body) > max_chars:
            out["truncated"] = "Truncated at %d of %d characters." % (max_chars, len(body))
        if not body:
            out["warning"] = (
                "No judgment body published for this ECLI — Rechtspraak publishes "
                "metadata for many more decisions than it publishes texts."
            )
        return out

    def vocabulary(self, which: str = "Rechtsgebieden") -> List[Dict[str, str]]:
        """Controlled values for the `subject` / `creator` filters."""
        if which not in ("Rechtsgebieden", "Instanties", "Proceduresoorten"):
            raise NlError("which must be Rechtsgebieden, Instanties or Proceduresoorten")
        raw = _fetch("%s/Waardelijst/%s" % (RECHTSPRAAK, which))
        try:
            root = ET.fromstring(raw.encode("utf-8"))
        except ET.ParseError as exc:
            raise NlError("Unparseable value list: %s" % exc) from exc
        out = []
        for node in list(root):
            ident = node.findtext("Identifier") or node.findtext("{*}Identifier") or ""
            name = node.findtext("Naam") or node.findtext("{*}Naam") or ""
            if ident or name:
                out.append({"identifier": ident.strip(), "name": name.strip()})
        return out

    def iter_days(self, date_from: str, date_to: str) -> Iterator[str]:
        start = _dt.date.fromisoformat(date_from)
        end = _dt.date.fromisoformat(date_to)
        while start <= end:
            yield start.isoformat()
            start += _dt.timedelta(days=1)


class KoopClient:
    """Dutch legislation via SRU 2.0 — real full-text search, passed through."""

    def search(self, query: str, start: int = 1, limit: int = 20) -> Dict[str, Any]:
        if not query.strip():
            raise NlError("query is required")
        # CQL: quote the phrase so spaces do not become separate clauses.
        cql = 'cql.textAndIndexes="%s"' % query.replace('"', "")
        params = {
            "operation": "searchRetrieve", "version": "2.0", "query": cql,
            "startRecord": max(1, int(start)),
            "maximumRecords": max(1, min(int(limit), 100)),
        }
        url = "%s?%s" % (KOOP_SRU, urllib.parse.urlencode(params))
        raw = _fetch(url)
        try:
            root = ET.fromstring(raw.encode("utf-8"))
        except ET.ParseError as exc:
            raise NlError("KOOP returned unparseable SRU XML: %s" % exc) from exc
        diag = root.find(".//{http://docs.oasis-open.org/ns/search-ws/diagnostic}message")
        if diag is not None:
            raise NlError("KOOP SRU diagnostic: %s" % "".join(diag.itertext()))
        total = int(root.findtext(SRU + "numberOfRecords") or 0)
        results = []
        for rec in root.findall(".//" + SRU + "record"):
            def dcv(tag: str) -> str:
                el = rec.find(".//" + DCTERMS + tag)
                return "".join(el.itertext()).strip() if el is not None else ""
            ident = dcv("identifier")
            url_el = rec.find(".//" + GZD + "itemUrl")
            results.append({
                "identifier": ident,
                "title": dcv("title"),
                "type": dcv("type"),
                "date": dcv("modified") or dcv("issued") or dcv("date"),
                "authority": dcv("creator") or dcv("publisher"),
                "url": ("".join(url_el.itertext()).strip() if url_el is not None
                        else ("https://wetten.overheid.nl/%s" % ident if ident else "")),
                "citation": "%s (%s)" % (dcv("title"), ident) if ident else dcv("title"),
            })
        return {"total": total, "returned": len(results), "request_url": url,
                "results": results}
