#!/usr/bin/env python3
"""Client for legislation.gov.uk — the UK statute book, published by The National
Archives under the Open Government Licence.

The National Archives publish their own MCP server (legislation-mcp-ts), but its
product policy binds it to loopback: it is not meant to be hosted for others.
This client speaks to the same public data over the site's open XML and Atom
endpoints instead, so nothing is redistributed and no policy is stretched.

The endpoint that matters most is the one nobody reaches for: /changes/affected,
which lists amendments made by later legislation and says whether each has been
applied to the revised text yet. Reading the text alone will silently miss them.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

BASE = "https://www.legislation.gov.uk"
UA = "ArthurLegal-MCP/1.0 (+https://github.com/beerbottle90/arthurlegal-mcp)"

ATOM = "{http://www.w3.org/2005/Atom}"
OS = "{http://a9.com/-/spec/opensearch/1.1/}"
LEG = "{http://www.legislation.gov.uk/namespaces/legislation}"
UKM = "{http://www.legislation.gov.uk/namespaces/metadata}"
DC = "{http://purl.org/dc/elements/1.1/}"

# The document types a caller is likely to want. legislation.gov.uk has many
# more; these are the ones that carry commercial governing law.
TYPES = {
    "ukpga": "UK Public General Act",
    "ukla": "UK Local Act",
    "uksi": "UK Statutory Instrument",
    "asp": "Act of the Scottish Parliament",
    "ssi": "Scottish Statutory Instrument",
    "anaw": "Act of Senedd Cymru",
    "wsi": "Wales Statutory Instrument",
    "nia": "Act of the Northern Ireland Assembly",
    "nisr": "Northern Ireland Statutory Rule",
    "eur": "Retained EU Regulation",
    "all": "every type",
}


class UkError(RuntimeError):
    pass


def _fetch(path: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UkError("not found: %s" % path) from exc
        raise UkError("legislation.gov.uk HTTP %d on %s" % (exc.code, path)) from exc
    except urllib.error.URLError as exc:
        raise UkError("legislation.gov.uk unreachable: %s" % exc.reason) from exc


def _xml(path: str, timeout: int = 60) -> ET.Element:
    raw = _fetch(path, timeout)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise UkError("legislation.gov.uk returned unparseable XML for %s" % path) from exc
    # Several .../data.xml paths quietly serve the web page instead of data.
    # An XHTML root is that failure, and it must not read as an empty result.
    if root.tag.endswith("}html"):
        raise UkError(
            "%s served an HTML page, not data. That path has no machine-readable "
            "form; use the data.feed variant." % path)
    return root


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _entry(node: ET.Element) -> Dict[str, Any]:
    title = _clean((node.findtext(ATOM + "title") or ""))
    ident = node.findtext(ATOM + "id") or ""
    year = node.find(UKM + "Year")
    number = node.find(UKM + "Number")
    doc_type = node.find(UKM + "DocumentMainType")
    return {
        "title": title,
        "uri": ident,
        "year": (year.get("Value") if year is not None else ""),
        "number": (number.get("Value") if number is not None else ""),
        "doc_type": (doc_type.get("Value") if doc_type is not None else ""),
        "updated": _clean(node.findtext(ATOM + "updated") or ""),
        "source_url": ident.replace("/id/", "/") if ident else "",
        "citation": title,
    }


class UkClient:
    # ---------------------------------------------------------------- search
    def search(self, title: str = "", text: str = "", doc_type: str = "all",
               year: str = "", page: int = 1) -> Dict[str, Any]:
        """Search the statute book.

        Year filtering goes in the PATH (/ukpga/1996/data.feed), not the query
        string. A `year=` parameter is accepted by the server and silently
        ignored -- same total, same first result -- so passing it there would
        return an unfiltered list that looks filtered. There is no range
        filter upstream; ask for one year at a time.
        """
        doc_type = (doc_type or "all").lower()
        if doc_type not in TYPES:
            raise UkError("unknown type %r; use one of: %s"
                          % (doc_type, ", ".join(sorted(TYPES))))
        if not any((title, text, year)):
            raise UkError("give at least one of title, text, year")
        params: Dict[str, Any] = {"page": max(1, int(page))}
        if title:
            params["title"] = title
        if text:
            params["text"] = text
        segment = "/%s/%s" % (doc_type, year) if year else "/%s" % doc_type
        path = "%s/data.feed?%s" % (segment, urllib.parse.urlencode(params))
        root = _xml(path)
        total = root.findtext(OS + "totalResults") or "0"
        results = [_entry(e) for e in root.findall(ATOM + "entry")]
        return {"total": int(total), "page": max(1, int(page)),
                "type": doc_type, "type_label": TYPES[doc_type],
                "year": year or "all years",
                "results": results}

    # ------------------------------------------------------------- one item
    def get(self, doc_type: str, year: str, number: str) -> Dict[str, Any]:
        path = "/%s/%s/%s/data.xml" % (doc_type, year, number)
        root = _xml(path, timeout=120)
        def _val(tag: str) -> str:
            # These live at varying depths inside Metadata depending on the
            # document; a fixed path finds nothing and reads as "not published".
            for node in root.iter(UKM + tag):
                return node.get("Value", "")
            return ""
        title = ""
        for node in root.iter(DC + "title"):
            title = _clean(node.text or "")
            break
        status = _val("DocumentStatus")
        return {
            "title": title,
            "doc_type": _val("DocumentMainType"),
            "year": year,
            "number": number,
            "status": status,
            "status_note": (
                "'revised' means amendments have been incorporated up to a "
                "point; 'final' (as enacted) means they have NOT. Either way, "
                "check get_effects for amendments not yet applied."
                if status else "legislation.gov.uk published no status for this item"),
            "valid_dates": _val("ValidDates"),
            # The date the revised text is current to. Anything enacted after
            # it is, by definition, not yet in the text you are reading.
            "text_current_to": root.get("RestrictStartDate", ""),
            "provisions": root.get("NumberOfProvisions", ""),
            "extent": root.get("RestrictExtent", ""),
            "source_url": "%s/%s/%s/%s" % (BASE, doc_type, year, number),
            "citation": title or "%s %s c. %s" % (doc_type, year, number),
        }

    def get_section(self, doc_type: str, year: str, number: str,
                    section: str) -> Dict[str, Any]:
        section = str(section).strip()
        path = "/%s/%s/%s/section/%s/data.xml" % (doc_type, year, number,
                                                  urllib.parse.quote(section))
        root = _xml(path, timeout=90)
        # The section document carries the whole Act's metadata block ahead of
        # the provision. Taking every text node returns the title and URI as if
        # they were part of the section, so read the Body element when present.
        node = None
        for candidate in root.iter(LEG + "Body"):
            node = candidate
            break
        body = _clean("".join((node if node is not None else root).itertext()))
        return {
            "doc_type": doc_type, "year": year, "number": number,
            "section": section,
            "text": body,
            "chars": len(body),
            "extracted_from": "Body element" if node is not None else "whole document (no Body element)",
            "source_url": "%s/%s/%s/%s/section/%s" % (BASE, doc_type, year, number, section),
        }

    def contents(self, doc_type: str, year: str, number: str) -> Dict[str, Any]:
        root = _xml("/%s/%s/%s/contents/data.xml" % (doc_type, year, number), timeout=120)
        items = []
        for node in root.iter():
            if node.tag == LEG + "ContentsItem":
                ref = node.get("IdURI", "") or node.get("DocumentURI", "")
                label = _clean("".join(node.itertext()))
                if label:
                    items.append({"label": label[:160], "uri": ref})
        return {"doc_type": doc_type, "year": year, "number": number,
                "items": items[:600], "returned": min(len(items), 600),
                "total": len(items)}

    # -------------------------------------------------------------- effects
    def effects(self, doc_type: str, year: str, number: str,
                applied: Optional[bool] = None, page: int = 1,
                max_pages: int = 20) -> Dict[str, Any]:
        """Amendments made to this item by later legislation.

        `applied=False` is the question that matters: the amendment is law, but
        the revised text on the site does not yet show it. Reading the text
        without checking is the standard way to advise on a superseded
        provision.

        When a filter is given the whole feed is walked, not just one page.
        Filtering a single page would answer "no unapplied amendments" from a
        page that happened to hold none -- the most confident possible way to be
        wrong here.
        """
        pages = range(max(1, int(page)), max(1, int(page)) + max(1, int(max_pages)))             if applied is not None else [max(1, int(page))]
        out: List[Dict[str, Any]] = []
        total = 0
        unapplied_total = 0
        scanned = 0
        exhausted = False
        for p in pages:
            path = "/changes/affected/%s/%s/%s/data.feed?page=%d" % (
                doc_type, year, number, p)
            root = _xml(path, timeout=120)
            if p == pages[0]:
                total = int(root.findtext(OS + "totalResults") or "0")
            entries = root.findall(ATOM + "entry")
            if not entries:
                exhausted = True
                break
            scanned += len(entries)
            for entry in entries:
                content = entry.find(ATOM + "content")
                effect = content.find(UKM + "Effect") if content is not None else None
                if effect is None:
                    continue
                a = effect.attrib
                is_applied = (a.get("Applied", "") or "").lower() == "true"
                if not is_applied:
                    unapplied_total += 1
                if applied is not None and is_applied != applied:
                    continue
                out.append({
                    "type": a.get("Type", ""),
                    "applied": is_applied,
                    "affected_provisions": a.get("AffectedProvisions", ""),
                    "affecting_title": _clean(entry.findtext(ATOM + "title") or ""),
                    "affecting_provisions": a.get("AffectingProvisions", ""),
                    "affecting_year": a.get("AffectingYear", ""),
                    "affecting_number": a.get("AffectingNumber", ""),
                    "affecting_uri": a.get("AffectingURI", ""),
                    "extent": a.get("AffectingEffectsExtent", ""),
                })
            if scanned >= total:
                exhausted = True
                break
        return {
            "doc_type": doc_type, "year": year, "number": number,
            "total_effects": total,
            "effects_scanned": scanned,
            "complete_scan": exhausted or applied is None and scanned >= total,
            "unapplied_found": unapplied_total,
            "filter": ("applied only" if applied is True else
                       "unapplied only" if applied is False else "none"),
            "returned": len(out),
            "results": out,
        }
