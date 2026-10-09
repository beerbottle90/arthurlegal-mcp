"""Client for Austria's RIS Open Government Data API — standard library only.

Three things this wrapper exists to handle, all found by testing the live API:

1. **RIS searches but does not rank.** ``Suchworte=Aktiengesetz`` returns 1,423
   genuine hits *in alphabetical order*, so the Aktiengesetz itself is not on the
   first page — the top three are "2. Wohnrechtsänderungsgesetz" and two EU
   association agreements. Every search here is reranked locally by BM25.

2. **``Kurztitel`` is silently ignored.** Passing it returns 441,066 hits — the
   entire corpus — rather than an error. A caller who trusted it would think they
   had filtered when they had not. This client never sends it.

3. **The version tuple moved.** ``v2.5`` now 404s; ``v2.6`` is current.

4. **Documents are files, not pages.** A result's ``url`` (``Dokument.wxe`` or
   ``eli/``) is the RIS web page, which answers 503 to automated clients; the
   same document is served as html/xml/rtf/pdf files under ``/Dokumente/``.
   The HTML file opens with ~4,000 characters of head and CSS, so a fetch
   returns extracted text unless the raw file is asked for (2026-10-09).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

__version__ = "1.0.0"

API = "https://data.bka.gv.at/ris/api/v2.6"
OGD = "https://ogd.ris.bka.gv.at"
UA = "arthurlegal-at-ris-mcp/%s (+https://github.com/beerbottle90/arthurlegal-mcp)" % __version__

# RIS pages in fixed sizes. Checked 2026-10-09: its PageSize enumeration is
# exactly these four; "Five", "Fifteen", "TwentyFive" and numerals all fail
# schema validation. Fewer results are a local cut (`limit` in server.py).
PAGE_SIZES = {10: "Ten", 20: "Twenty", 50: "Fifty", 100: "OneHundred"}

# Applikation -> what it actually covers. Used to validate input and to explain
# the choice to the model.
LEGISLATION_APPS = {
    "BrKons": "Bundesrecht konsolidiert — consolidated federal law (default)",
    "BgblAuth": "Bundesgesetzblatt authentisch — the authentic gazette since 2004",
    "LrKons": "Landesrecht konsolidiert — consolidated provincial law",
}
CASELAW_APPS = {
    "Justiz": "OGH (Supreme Court) and the ordinary civil/criminal courts",
    "Vwgh": "Verwaltungsgerichtshof — Supreme Administrative Court",
    "Vfgh": "Verfassungsgerichtshof — Constitutional Court",
    "Bvwg": "Bundesverwaltungsgericht — Federal Administrative Court",
    "Lvwg": "Landesverwaltungsgerichte — provincial administrative courts",
}

# A Rechtssatz lists every decision that applied it, each with a note: one OGH
# proposition found under "Schadenersatz" lists 275, and ten such hits made a
# 256,833-character answer (2026-10-09). Search results keep the first few
# decisions (where the line starts) and the latest few (that it still holds),
# with the total; the Rechtssatz document itself has the full list.
DECISIONS_FIRST = 3
DECISIONS_LATEST = 2
DOCKETS_SHOWN = 3
NOTE_CHARS = 200


def _keywords(value: Any) -> str:
    """RIS Schlagworte as one comma list; RIS often repeats the whole list twice."""
    seen: List[str] = []
    for word in re.split(r"[,\r\n]+", str(value or "")):
        word = word.strip()
        if word and word not in seen:
            seen.append(word)
    return ", ".join(seen)


def _clip(text: str, limit: int) -> str:
    # RIS notes carry inline <br/> between Beisätze.
    text = " ".join(re.sub(r"<[^>]+>", " ", str(text or "")).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class RisError(Exception):
    """An upstream failure worth explaining to the caller."""


def _get(url: str, timeout: int = 60) -> Dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RisError(
                "RIS returned 404 for %s. Note that API v2.5 was retired — this "
                "client uses v2.6." % url
            ) from exc
        raise RisError("HTTP %s from RIS: %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise RisError("Could not reach RIS: %s" % exc.reason) from exc
    except ValueError as exc:
        raise RisError("RIS returned unparseable JSON: %s" % exc) from exc


def _listify(value: Any) -> List[Any]:
    """RIS collapses single-element arrays into a bare object. Undo that."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _items(value: Any) -> List[str]:
    """RIS wraps repeated values as ``{"item": x}`` or ``{"item": [x, y]}``."""
    if value is None:
        return []
    if isinstance(value, dict):
        value = value.get("item")
    return [str(v) for v in _listify(value) if v not in (None, "")]


class _Text(HTMLParser):
    """Readable text from a RIS document file, HTML or XML rendition.

    Drops what is never document text: the HTML head with its script and CSS;
    the screen-reader copies RIS puts beside every § and Roman numeral
    ("Paragraph 5," next to "§ 5.", "römisch zwei c" next to "IIc"); and the
    XML rendition's running page headers and footers ("Seite 1 von 2").
    Block elements become line breaks.
    """

    SKIP = {"head", "script", "style", "kzinhalt", "fzinhalt"}
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
             "table", "ol", "ul", "absatz", "ueberschrift", "abstand"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip_tag = ""
        self._depth = 0
        self._spans: List[bool] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if self._depth:
            if tag == self._skip_tag:
                self._depth += 1
            return
        classes = (dict(attrs).get("class") or "").split()
        if tag in self.SKIP or "sr-only" in classes:
            self._skip_tag, self._depth = tag, 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th", "tab"):
            self.parts.append(" ")
        elif tag == "span":
            # Paragraph numbers ("(1)") sit flush against their text; CSS spaces them.
            self._spans.append("Absatzzahl" in classes)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if not self._depth:
            if tag in self.BLOCK:
                self.parts.append("\n")
            elif tag == "tab":
                self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._depth:
            if tag == self._skip_tag:
                self._depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag == "span" and self._spans and self._spans.pop():
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._depth:
            self.parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line)


def _to_text(markup: str) -> str:
    parser = _Text()
    parser.feed(markup)
    parser.close()
    return parser.text()


def _document_file(url: str, raw: bool) -> Tuple[str, str]:
    """The /Dokumente/ file behind a RIS URL, with a note when it differs.

    ``Dokument.wxe?Abfrage=X&Dokumentnummer=Y`` is served as
    ``/Dokumente/X/Y/Y.html`` (checked for Justiz, Vwgh, Vfgh, Bvwg, Lvwg and
    the decisions listed in a Rechtssatz); an ``eli/`` URL ending in a federal
    norm number (NOR...) as ``/Dokumente/Bundesnormen/NOR.../NOR....html``.
    PDF and RTF are read from the HTML rendition unless the raw file is wanted.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.path.endswith("/Dokument.wxe"):
        query = urllib.parse.parse_qs(parts.query)
        app = (query.get("Abfrage") or [""])[0]
        number = (query.get("Dokumentnummer") or [""])[0]
        if re.fullmatch(r"\w+", app) and re.fullmatch(r"\w+", number):
            return ("%s/Dokumente/%s/%s/%s.html" % (OGD, app, number, number),
                    "Read from the document's HTML file; the RIS web page "
                    "refuses automated clients.")
    norm = re.search(r"/eli/.+/(NOR\d+)/?$", parts.path)
    if norm:
        number = norm.group(1)
        return ("%s/Dokumente/Bundesnormen/%s/%s.html" % (OGD, number, number),
                "Read from the norm's HTML file; the RIS web page refuses "
                "automated clients.")
    if not raw and re.search(r"\.(pdf|rtf)$", parts.path, re.IGNORECASE):
        return (re.sub(r"\.(pdf|rtf)$", ".html", url, flags=re.IGNORECASE),
                "Read from the HTML rendition of the same document; PDF and "
                "RTF are not text.")
    return url, ""


def _content_urls(data: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    ref = (data.get("Dokumentliste") or {}).get("ContentReference") or {}
    for r in _listify(ref):
        for url in _listify((r.get("Urls") or {}).get("ContentUrl")):
            if isinstance(url, dict) and url.get("DataType"):
                out[str(url["DataType"]).lower()] = url.get("Url", "")
    return out


class RisClient:
    def _search(self, app_group: str, application: str, params: Dict[str, Any],
                page_size: int, page: int) -> Dict[str, Any]:
        size = PAGE_SIZES.get(int(page_size))
        if size is None:
            raise RisError(
                "page_size must be one of %s — the only page sizes RIS accepts. "
                "For fewer results, keep a page size and set `limit`." % sorted(PAGE_SIZES))
        query = {"Applikation": application, "DokumenteProSeite": size,
                 "Seitennummer": max(1, int(page))}
        query.update({k: v for k, v in params.items() if v})
        url = "%s/%s?%s" % (API, app_group, urllib.parse.urlencode(query))
        payload = _get(url)
        envelope = payload.get("OgdSearchResult") or {}
        if envelope.get("Error"):
            # RIS reports a rejected request inside an HTTP 200; read as a
            # result it looked like zero hits.
            error = envelope["Error"]
            message = error.get("Message") if isinstance(error, dict) else error
            raise RisError("RIS rejected the request: %s (%s)" % (message, url))
        results = envelope.get("OgdDocumentResults") or {}
        hits = results.get("Hits") or {}
        try:
            total = int(hits.get("#text", 0))
        except (TypeError, ValueError):
            total = 0
        return {"total": total, "refs": _listify(results.get("OgdDocumentReference")),
                "request_url": url}

    # -- legislation ------------------------------------------------------ #
    def search_legislation(self, terms: str = "", title: str = "",
                           application: str = "BrKons", as_of: str = "",
                           page_size: int = 20, page: int = 1) -> Dict[str, Any]:
        if application not in LEGISLATION_APPS:
            raise RisError("application must be one of %s" % sorted(LEGISLATION_APPS))
        if not terms and not title:
            raise RisError("Provide `terms` (full text) or `title`.")
        params: Dict[str, Any] = {"Suchworte": terms, "Titel": title}
        if as_of:
            # Point-in-time: what the law looked like on this date.
            params["Fassung.FassungVom"] = as_of
        # Provincial law has its own endpoint; Bundesrecht rejects LrKons.
        group = "Landesrecht" if application == "LrKons" else "Bundesrecht"
        raw = self._search(group, application, params, page_size, page)
        return {**raw, "results": [self._norm_law(r) for r in raw["refs"]]}

    def _norm_law(self, ref: Dict[str, Any]) -> Dict[str, Any]:
        data = ref.get("Data") or {}
        meta = data.get("Metadaten") or {}
        tech = meta.get("Technisch") or {}
        gen = meta.get("Allgemein") or {}
        law = meta.get("Bundesrecht") or meta.get("Landesrecht") or {}
        sub = law.get("BrKons") or law.get("LrKons") or law.get("BgblAuth") or {}
        eli = law.get("Eli") or gen.get("DokumentUrl") or ""
        short = law.get("Kurztitel") or ""
        # Each consolidated hit is ONE provision in ONE version: "§ 1295",
        # "Art. 8", "Anl. 1". Dokumenttyp "Norm" is the act-level entry RIS
        # numbers "§ 0" — the act itself, not a paragraph to cite.
        doc_type = sub.get("Dokumenttyp", "")
        section = "" if doc_type == "Norm" else (sub.get("ArtikelParagraphAnlage") or "").strip()
        # A gazette issue (BgblAuth) is identified by its BGBl number instead.
        gazette = (sub.get("Kundmachungsorgan") or sub.get("Bgblnummer") or "").strip()
        return {
            "id": tech.get("ID", ""),
            "title": short,
            "section": section,
            "doc_type": doc_type,
            # Validity of this version; empty valid_to means still in force.
            "valid_from": sub.get("Inkrafttretensdatum", ""),
            "valid_to": sub.get("Ausserkrafttretensdatum", ""),
            "keywords": _keywords(sub.get("Schlagworte")),
            # RIS embeds <br/> and the enacting history in the long title.
            "long_title": (law.get("Titel") or "").replace("<br/>", " · "),
            "eli": eli,
            "url": gen.get("DokumentUrl") or eli,
            "gazette": gazette,
            "type": sub.get("Typ", ""),
            "changed": gen.get("Geaendert", ""),
            "formats": _content_urls(data),
            # Built from fields RIS returned, never invented.
            "citation": "%s%s%s (RIS %s)" % (
                short or "(untitled)",
                " " + section if section else "",
                ", " + gazette if gazette else "",
                tech.get("ID", ""),
            ),
        }

    # -- case law --------------------------------------------------------- #
    def search_caselaw(self, terms: str = "", application: str = "Justiz",
                       date_from: str = "", date_to: str = "",
                       page_size: int = 20, page: int = 1) -> Dict[str, Any]:
        if application not in CASELAW_APPS:
            raise RisError("application must be one of %s" % sorted(CASELAW_APPS))
        if not terms:
            raise RisError("`terms` is required for case-law search.")
        params: Dict[str, Any] = {"Suchworte": terms}
        if date_from:
            params["Entscheidungsdatum.Von"] = date_from
        if date_to:
            params["Entscheidungsdatum.Bis"] = date_to
        raw = self._search("Judikatur", application, params, page_size, page)
        return {**raw, "results": [self._norm_case(r, application) for r in raw["refs"]]}

    def _norm_case(self, ref: Dict[str, Any], application: str) -> Dict[str, Any]:
        data = ref.get("Data") or {}
        meta = data.get("Metadaten") or {}
        tech = meta.get("Technisch") or {}
        gen = meta.get("Allgemein") or {}
        jud = meta.get("Judikatur") or {}
        # Court-specific fields live in a sub-object named after the application;
        # the identifying fields sit directly on Judikatur.
        sub = jud.get(application) or {}
        if not sub:
            for key in ("Justiz", "Vwgh", "Vfgh", "Bvwg", "Lvwg"):
                if jud.get(key):
                    sub = jud[key]
                    break

        doc_type = jud.get("Dokumenttyp", "")
        court = sub.get("Gericht") or tech.get("Organ") or application
        docket = _items(jud.get("Geschaeftszahl"))
        norms = _items(jud.get("Normen"))
        decided = jud.get("Entscheidungsdatum", "")
        ecli = jud.get("EuropeanCaseLawIdentifier", "")

        out: Dict[str, Any] = {
            "id": tech.get("ID", ""),
            "court": court,
            "doc_type": doc_type,
            "docket": "; ".join(docket),
            "date": decided,
            "ecli": ecli,
            "norms": norms,
            "legal_areas": _items(sub.get("Rechtsgebiete")),
            "url": gen.get("DokumentUrl", ""),
            "formats": _content_urls(data),
        }

        if doc_type == "Rechtssatz":
            # A Rechtssatz is a legal proposition distilled from a line of cases,
            # not a judgment. Saying so matters: citing it as "the decision" would
            # misdescribe what it is, and it lists every case that applied it.
            out["rechtssatz_numbers"] = _items(sub.get("Rechtssatznummern"))
            # RIS packs every docket of the line into one string.
            all_dockets = [x.strip() for x in "; ".join(docket).split(";") if x.strip()]
            if len(all_dockets) > DOCKETS_SHOWN:
                out["docket"] = "%s … (+%d)" % ("; ".join(all_dockets[:DOCKETS_SHOWN]),
                                               len(all_dockets) - DOCKETS_SHOWN)
            decisions = []
            for d in _listify((sub.get("Entscheidungstexte") or {}).get("item")):
                if isinstance(d, dict):
                    decisions.append(
                        {
                            "docket": d.get("Geschaeftszahl", ""),
                            "court": d.get("Gericht", ""),
                            "date": d.get("Entscheidungsdatum", ""),
                            "url": d.get("DokumentUrl", ""),
                            "note": _clip(d.get("Anmerkung", ""), NOTE_CHARS),
                        }
                    )
            total = len(decisions)
            if total > DECISIONS_FIRST + DECISIONS_LATEST:
                # RIS appends each new decision, so the tail is the latest.
                decisions = decisions[:DECISIONS_FIRST] + decisions[-DECISIONS_LATEST:]
                out["decisions_omitted"] = total - len(decisions)
            out["decisions"] = decisions
            out["decisions_total"] = total
            out["note"] = (
                "Rechtssatz — a legal proposition abstracted from %d decision(s), "
                "not a single judgment. Cite the underlying decision from "
                "`decisions` when you need a judgment." % total
            )
            if total > len(decisions):
                out["note"] += (
                    " Shown: the first %d and the latest %d; the full list is in "
                    "the Rechtssatz document (fetch_document with formats.html)."
                    % (DECISIONS_FIRST, DECISIONS_LATEST))
            rs_no = out["rechtssatz_numbers"]
            out["citation"] = " ".join(
                x for x in (court, rs_no[0] if rs_no else "", ecli) if x
            ) or "%s (RIS %s)" % (court, tech.get("ID", ""))
        else:
            out["citation"] = " ".join(
                x for x in (court, "; ".join(docket), decided, ecli) if x
            ) or "%s (RIS %s)" % (court, tech.get("ID", ""))
        return out

    # -- full text -------------------------------------------------------- #
    def fetch(self, url: str, max_chars: int = 60000, raw: bool = False) -> Dict[str, Any]:
        """Fetch a RIS document: readable text by default, the file as served if ``raw``."""
        if not url.startswith(("https://ogd.ris.bka.gv.at/", "https://www.ris.bka.gv.at/")):
            # Refuse to be a generic fetcher: this server speaks for RIS only.
            raise RisError("Refusing to fetch a non-RIS URL: %s" % url)
        target, note = _document_file(url, raw)
        req = urllib.request.Request(target, method="GET")
        req.add_header("User-Agent", UA)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8", "replace")
                ctype = (resp.headers.get("Content-Type") or "").lower()
        except urllib.error.HTTPError as exc:
            if exc.code == 503 and "/Dokumente/" not in target:
                raise RisError(
                    "RIS answered 503 for %s: its web pages refuse automated "
                    "clients. Fetch one of the result's `formats` URLs (html or "
                    "xml) instead." % url) from exc
            raise RisError("HTTP %s fetching %s" % (exc.code, target)) from exc
        except urllib.error.URLError as exc:
            raise RisError("Could not reach RIS: %s" % exc.reason) from exc
        path = urllib.parse.urlsplit(target).path.lower()
        if raw:
            fmt, text = "raw", body
        elif "xml" in ctype or path.endswith(".xml"):
            fmt, text = "xml", _to_text(body)
        elif "html" in ctype or path.endswith((".html", ".htm")):
            fmt, text = "html", _to_text(body)
        else:
            fmt, text = "raw", body
        out: Dict[str, Any] = {"url": url, "format": fmt, "length_chars": len(text),
                               "text": text[:max_chars]}
        if target != url:
            out["fetched_url"] = target
        if note:
            out["note"] = note
        if len(text) > max_chars:
            out["truncated"] = "Truncated at %d of %d characters." % (max_chars, len(text))
        return out
