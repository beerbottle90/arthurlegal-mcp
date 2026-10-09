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

``KoopClient`` — legislation (KOOP SRU: BWB + publications repository)
-----------------------------------------------------------------------
The opposite situation: real upstream search, no local index needed. KOOP runs
two SRU services and they answer different questions:

- **BWB** (zoekservice.overheid.nl, ``x-connection=BWB``) is the consolidated
  law behind wetten.overheid.nl: one record per *version* of a regulation,
  searched by title or abbreviation. This is the default. Without a validity
  date it returns every historical version (Burgerlijk Wetboek Boek 6 has 66),
  so the client always asks for the version valid on one day.
- **The publications repository** (repository.overheid.nl, SRU 2.0) holds the
  official gazettes and parliamentary papers, with real CQL full-text search
  (``energiewet`` -> 1,961 records). It is the wrong default for "find the
  law": ``Burgerlijk Wetboek Boek 6`` there returns 228 records led by three
  Staatscourant notices on "Consignatie van gelden" (verified 2026-10-09).
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
KOOP_BWB_SRU = "https://zoekservice.overheid.nl/sru/Search"
WETTEN = "https://wetten.overheid.nl"
UA = ("arthurlegal-nl-rechtspraak-mcp/%s "
      "(+https://github.com/beerbottle90/arthurlegal-mcp)" % __version__)

ATOM = "{http://www.w3.org/2005/Atom}"
DCTERMS = "{http://purl.org/dc/terms/}"
PSI = "{http://psi.rechtspraak.nl/}"
SRU = "{http://docs.oasis-open.org/ns/search-ws/sruResponse}"
SRW = "{http://www.loc.gov/zing/srw/}"        # SRU 1.2, which BWB still speaks
GZD = "{http://standaarden.overheid.nl/sru}"
OWMS = "{http://standaarden.overheid.nl/owms/terms/}"
BWB = "{http://standaarden.overheid.nl/bwb/terms/}"
SRU_DIAGNOSTICS = ("{http://docs.oasis-open.org/ns/search-ws/diagnostic}",
                   "{http://www.loc.gov/zing/srw/diagnostic/}")

ECLI_RE = re.compile(r"^ECLI:NL:[A-Z]+:\d{4}:[A-Z0-9.]+$", re.IGNORECASE)
BWB_ID_RE = re.compile(r"^BWBR\d{7}$", re.IGNORECASE)
# Atom titles read "ECLI:NL:HR:2026:919, Hoge Raad, 12-06-2026, 24/04627"; the
# content record writes the same as "ECLI:NL:HR:2026:919 Hoge Raad , 12-06-2026 / ...".
TITLE_COURT_RE = re.compile(r"^ECLI:\S+?[,\s]\s*(.+?)\s*,\s*\d{2}-\d{2}-\d{4}")

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


def court_from_title(title: str) -> str:
    """The court named in a Rechtspraak title, or "" when the title has another shape.

    Search entries carry no separate court field, but every title names it. The
    summary crawl used to store nothing, so this is also how rows indexed
    before that fix get their court back without a re-crawl.
    """
    m = TITLE_COURT_RE.match((title or "").strip())
    return m.group(1).strip() if m else ""


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
            title = (entry.findtext(ATOM + "title") or "").strip()
            results.append({
                "ecli": ecli,
                "title": title,
                "court": court_from_title(title),
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
        # dcterms:abstract is only a pointer ("../../rs:inhoudsindicatie"); the
        # official summary itself is the <inhoudsindicatie> element.
        summary_el = root.find(".//{*}inhoudsindicatie")
        summary = (_plain(ET.tostring(summary_el, encoding="unicode"))
                   if summary_el is not None else "")

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
            "abstract": summary or dc("abstract"),
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


def _sru_root(url: str) -> ET.Element:
    """Fetch an SRU response; a diagnostic is an error, never an empty result."""
    raw = _fetch(url)
    try:
        root = ET.fromstring(raw.encode("utf-8"))
    except ET.ParseError as exc:
        raise NlError("KOOP returned unparseable SRU XML: %s" % exc) from exc
    for ns in SRU_DIAGNOSTICS:
        diag = root.find(".//" + ns + "message")
        if diag is not None:
            details = (root.findtext(".//" + ns + "details") or "").strip()
            raise NlError("KOOP SRU diagnostic: %s%s" % (
                "".join(diag.itertext()), " (%s)" % details if details else ""))
    return root


def _text(node: ET.Element, path: str) -> str:
    el = node.find(path)
    return "".join(el.itertext()).strip() if el is not None else ""


class KoopClient:
    """Dutch legislation via KOOP SRU — see the module docstring for the two sources."""

    SOURCES = ("consolidated", "official_publications")

    def search(self, query: str, start: int = 1, limit: int = 20,
               source: str = "consolidated", as_of: str = "") -> Dict[str, Any]:
        if not query.strip():
            raise NlError("query is required")
        if source == "consolidated":
            return self._search_bwb(query, start, limit, as_of)
        if source == "official_publications":
            return self._search_publications(query, start, limit)
        raise NlError("source must be one of: %s" % ", ".join(self.SOURCES))

    def _search_bwb(self, query: str, start: int, limit: int, as_of: str) -> Dict[str, Any]:
        day = (as_of or _dt.date.today().isoformat()).strip()
        try:
            _dt.date.fromisoformat(day)
        except ValueError:
            raise NlError("as_of must be a date as YYYY-MM-DD, got %r" % as_of) from None
        # Inside a quoted CQL term only the quote and the escape character matter.
        terms = " ".join(re.sub(r'["\\]', " ", query).split())
        if BWB_ID_RE.match(terms):
            clause = 'dcterms.identifier = "%s"' % terms.upper()
        else:
            # All title words, or the official abbreviation ("Awb", "Sr").
            clause = ('(overheidbwb.titel all "%s" or overheidbwb.afkorting = "%s")'
                      % (terms, terms))
        # One version per regulation: the one valid on `day`. Without this BWB
        # returns every historical version as a separate record.
        cql = '%s and overheidbwb.geldigheidsdatum = "%s"' % (clause, day)
        params = {
            "x-connection": "BWB", "operation": "searchRetrieve", "version": "1.2",
            "query": cql, "startRecord": max(1, int(start)),
            "maximumRecords": max(1, min(int(limit), 100)),
        }
        url = "%s?%s" % (KOOP_BWB_SRU, urllib.parse.urlencode(params))
        root = _sru_root(url)
        total = int(root.findtext(SRW + "numberOfRecords") or 0)
        results: List[Dict[str, Any]] = []
        seen = set()
        for rec in root.iter(SRW + "record"):
            ident = _text(rec, ".//" + DCTERMS + "identifier").upper()
            if not ident or ident in seen:
                continue
            seen.add(ident)
            title = _text(rec, ".//" + DCTERMS + "title")
            valid_from = _text(rec, ".//" + BWB + "geldigheidsperiode_startdatum")
            valid_to = _text(rec, ".//" + BWB + "geldigheidsperiode_einddatum")
            results.append({
                "identifier": ident,
                "title": title,
                "type": _text(rec, ".//" + DCTERMS + "type"),
                "authority": _text(rec, ".//" + OWMS + "authority"),
                "date": valid_from,
                # Validity of the returned version, not of the regulation.
                "valid_from": valid_from,
                "valid_to": "" if valid_to.startswith("9999") else valid_to,
                "legal_areas": ["".join(el.itertext()).strip()
                                for el in rec.iter(BWB + "rechtsgebied")],
                # Permanent address: always resolves to the version in force.
                "url": "%s/%s" % (WETTEN, ident),
                "version_url": "%s/%s/%s" % (WETTEN, ident, valid_from) if valid_from else "",
                "xml_url": _text(rec, ".//" + BWB + "locatie_toestand"),
                "citation": "%s (%s)" % (title, ident),
            })
        # BWB already tends to rank the exact title first; make sure of it.
        wanted = terms.lower()
        results.sort(key=lambda r: r["title"].lower() != wanted)
        return {"source": "consolidated", "total": total, "returned": len(results),
                "as_of": day, "request_url": url, "results": results}

    def _search_publications(self, query: str, start: int, limit: int) -> Dict[str, Any]:
        # CQL: quote the phrase so spaces do not become separate clauses.
        cql = 'cql.textAndIndexes="%s"' % query.replace('"', "")
        params = {
            "operation": "searchRetrieve", "version": "2.0", "query": cql,
            "startRecord": max(1, int(start)),
            "maximumRecords": max(1, min(int(limit), 100)),
        }
        url = "%s?%s" % (KOOP_SRU, urllib.parse.urlencode(params))
        root = _sru_root(url)
        total = int(root.findtext(SRU + "numberOfRecords") or 0)
        results = []
        for rec in root.findall(".//" + SRU + "record"):
            def dcv(tag: str) -> str:
                return _text(rec, ".//" + DCTERMS + tag)
            ident = dcv("identifier")
            # The publication's own page (officielebekendmakingen.nl). These
            # are not consolidated law and have no wetten.overheid.nl address.
            url_el = rec.find(".//" + GZD + "preferredUrl")
            if url_el is None:
                url_el = rec.find(".//" + GZD + "itemUrl")
            results.append({
                "identifier": ident,
                "title": dcv("title"),
                "type": dcv("type"),
                "date": dcv("modified") or dcv("issued") or dcv("date"),
                "authority": dcv("creator") or dcv("publisher"),
                "url": "".join(url_el.itertext()).strip() if url_el is not None else "",
                "citation": "%s (%s)" % (dcv("title"), ident) if ident else dcv("title"),
            })
        return {"source": "official_publications", "total": total,
                "returned": len(results), "request_url": url, "results": results}
