"""Client for the Irish Statute Book — standard library only.

The Statute Book publishes clean, server-rendered ELI documents:

    /eli/{year}/act/{no}/enacted/en/html              the Act's contents page
    /eli/{year}/act/{no}/enacted/en/print.html        the whole Act ("View Full Act")
    /eli/{year}/act/{no}/section/{n}/enacted/en/html  one section

The first is easy to mistake for the Act: it is the table of contents and the
long title, without a word of section text (No. 44 of 2024: 8,959 characters,
against ~74,000 in the print view). Every page wraps the document in the same
site menu and footer, in English and Irish; the document itself sits in
``<div class="act-content" id="act">``, which is all this client reads.

What it does **not** publish is a search endpoint — ``/search`` and
``/searchresults.html`` both 404 — so search is served from a local index built
by ``crawl.py``.

Two cautions this client encodes:

* ``enacted`` is the Act **as passed**. Amendments are not applied. The Revised
  Acts collection is separate and does not cover everything, so every response
  says which version it is rather than leaving the reader to assume.
* Section-level fetching is the right default. The Companies Act 2014 runs to
  1,448 sections; pulling the whole thing to answer a question about one of them
  wastes the context it would need to answer well.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

__version__ = "1.1.0"

BASE = "https://www.irishstatutebook.ie"
UA = ("arthurlegal-ie-statutebook-mcp/%s "
      "(+https://github.com/beerbottle90/arthurlegal-mcp)" % __version__)

VERSIONS = ("enacted", "revised")


class IeError(Exception):
    """An upstream failure worth explaining to the caller."""


def _fetch(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise IeError("Not found (404): %s" % url) from exc
        raise IeError("HTTP %s from the Irish Statute Book: %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise IeError("Could not reach irishstatutebook.ie: %s" % exc.reason) from exc


_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
# Tags that end a line of the document; every other tag is inline.
_BREAK = re.compile(r"<\s*(?:br|hr)\b[^>]*>|<\s*/\s*(?:p|tr|div|h[1-6]|li|table|blockquote)\s*>", re.I)
# Tags that separate words without ending a line (table cells, images).
_SPACER = re.compile(r"<\s*/?\s*(?:td|th|img)\b[^>]*>", re.I)
_CONTENT = re.compile(r'<div\b[^>]*\bclass="act-content"[^>]*>', re.I)
_DIV = re.compile(r"<(/?)div\b[^>]*>", re.I)


# Site chrome that appears on every page, in English and Irish. Left in, it
# would be the most common phrase in the corpus and would pollute BM25 scoring.
# Only the fallback below needs it: pages with an act-content block never
# reach the chrome at all.
_CHROME = re.compile(
    r"(Skip to content|Disclaimer|Feedback|Helpdesk|Gaeilge|"
    r"Léim go dtí an t-ábhar|Séanadh|Aiseolas|Deasc chabhrach|"
    r"Baile|Home|Irish Statute Book|Print|Share)",
    re.I,
)


def _content_html(page: str) -> str:
    """The inside of ``<div class="act-content" id="act">``, or "" if absent.

    Found by counting nested divs from the opening tag, so the site menu above
    it and the footer below it are never read.
    """
    start = _CONTENT.search(page)
    if not start:
        return ""
    depth = 1
    for m in _DIV.finditer(page, start.end()):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return page[start.end():m.start()]
    return page[start.end():]


def _html_text(fragment: str) -> str:
    """Readable text with one line per paragraph or table row."""
    text = _SCRIPT.sub(" ", fragment)
    # Line breaks in the HTML source are not paragraph breaks.
    text = re.sub(r"\s+", " ", text)
    text = _BREAK.sub("\n", text)
    text = _SPACER.sub(" ", text)
    text = _TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _plain(page: str) -> str:
    content = _content_html(page)
    if content:
        return _html_text(content)
    text = _SCRIPT.sub(" ", page)
    text = _TAG.sub(" ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    # Strip the leading navigation run, not every occurrence: the words can also
    # appear legitimately inside a section's text.
    head, tail = text[:400], text[400:]
    head = _CHROME.sub(" ", head)
    return re.sub(r"\s+", " ", head + tail).strip()


# Contents entries: <a href="...#sec12A" ...>12A. Title</a>, and the schedules.
_TOC_SECTION = re.compile(
    r'<a\s[^>]*href="[^"#]*#sec([0-9]+[A-Za-z]*)"[^>]*>\s*([0-9]+[A-Za-z]*)\.\s*([^<]*?)\s*</a>',
    re.I)
_TOC_SCHEDULE = re.compile(
    r'<a\s[^>]*href="[^"#]*#sched[^"]*"[^>]*value1="Schedule"[^>]*>\s*([^<]+?)\s*</a>', re.I)


def _contents(content: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """The Act's section index and schedule names, from its contents list."""
    sections: List[Dict[str, str]] = []
    seen = set()
    for anchor, number, title in _TOC_SECTION.findall(content):
        if anchor.lower() != number.lower() or number in seen:
            continue      # a cross-reference, or the same entry again
        seen.add(number)
        sections.append({"number": number, "title": html.unescape(title).strip()})
    schedules: List[str] = []
    for name in _TOC_SCHEDULE.findall(content):
        name = html.unescape(name).strip()
        if name not in schedules:
            schedules.append(name)
    return sections, schedules


def _title(page: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    return html.unescape(m.group(1)).strip() if m else ""


_MONTHS = {name: i for i, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), 1)}
_BRACKETED = re.compile(r"\[([^\[\]]{6,40})\]")
_LONG_TITLE_DATE = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?([a-z]+),?(\d{4})$")
_ELI_DATE = re.compile(
    r'<meta[^>]+property="eli:date_document"[^>]+content="(\d{4}-\d{2}-\d{2})"', re.I)
_LD_DATE = re.compile(r'"legislationDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"')


def date_from_text(text: str) -> str:
    """ISO date from the long title's closing "[12th November, 2024]", or "".

    The rendered pages break the date with markup ("[2 nd Jun e, 2022]",
    "[11 thJuly , 2023]" in the 2022 and 2023 Acts), so whitespace is dropped
    before it is read. The first such date precedes "Be it enacted".
    """
    head = text.split("Be it enacted", 1)[0] if "Be it enacted" in text else text
    for chunk in (head, text):
        for m in _BRACKETED.finditer(chunk):
            parts = _LONG_TITLE_DATE.match(re.sub(r"\s+", "", m.group(1)).lower())
            if parts and parts.group(2) in _MONTHS:
                day, month, year = int(parts.group(1)), _MONTHS[parts.group(2)], int(parts.group(3))
                if 1 <= day <= 31:
                    return "%04d-%02d-%02d" % (year, month, day)
    return ""


def _dated(page: str) -> Tuple[str, str]:
    """``(date, source)`` of the Act's enactment, from the page itself.

    ELI metadata first (``eli:date_document``), then the schema.org block,
    then the long title. Never a placeholder: an unknown date is ``("", "")``.
    """
    for rx, source in ((_ELI_DATE, "eli:date_document"), (_LD_DATE, "schema:legislationDate")):
        m = rx.search(page)
        if m:
            return m.group(1), source
    date = date_from_text(_plain(page))
    return (date, "long title") if date else ("", "")


def _enacted_date(page: str) -> str:
    return _dated(page)[0]


class StatuteBookClient:
    def act_url(self, year: int, number: int, version: str = "enacted") -> str:
        if version not in VERSIONS:
            raise IeError("version must be 'enacted' or 'revised'")
        return "%s/eli/%d/act/%d/%s/en/html" % (BASE, int(year), int(number), version)

    def print_url(self, year: int, number: int, version: str = "enacted") -> str:
        """The whole Act: where the contents page's "View Full Act" link points."""
        if version not in VERSIONS:
            raise IeError("version must be 'enacted' or 'revised'")
        return "%s/eli/%d/act/%d/%s/en/print.html" % (BASE, int(year), int(number), version)

    def section_url(self, year: int, number: int, section: str,
                    version: str = "enacted") -> str:
        if version not in VERSIONS:
            raise IeError("version must be 'enacted' or 'revised'")
        # Sections can be '12', '12A' or '348bis'-style; keep it as given.
        safe = re.sub(r"[^0-9A-Za-z]", "", str(section))
        if not safe:
            raise IeError("section must be a section number, e.g. 1 or 12A")
        return "%s/eli/%d/act/%d/section/%s/%s/en/html" % (
            BASE, int(year), int(number), safe, version)

    def get_act(self, year: int, number: int, version: str = "enacted",
                max_chars: int = 60000, offset: int = 0,
                sections_limit: int = 200) -> Dict[str, Any]:
        """The whole Act: text from the print view, plus its section index.

        ``offset`` pages through a long Act; ``next_offset`` says where the next
        window starts. The index lists sections by number and heading so a
        caller can go straight to ``get_section``.
        """
        url = self.print_url(year, number, version)
        page = _fetch(url)
        content = _content_html(page)
        body = _html_text(content) if content else _plain(page)
        title = _title(page)
        start = max(0, int(offset))
        window = body[start:start + max(1, int(max_chars))]
        sections, schedules = _contents(content)
        out: Dict[str, Any] = {
            "year": int(year),
            "number": int(number),
            "version": version,
            "title": title,
            "date_enacted": _enacted_date(page),
            "url": url,
            "contents_url": self.act_url(year, number, version),
            "citation": "%s (No. %d of %d)" % (title or "Act", int(number), int(year)),
            "length_chars": len(body),
            "offset": start,
            "text": window,
            "sections_total": len(sections),
            "sections": sections[:max(0, int(sections_limit))],
            "schedules": schedules,
        }
        if not out["date_enacted"]:
            out["date_note"] = "The page states no enactment date; none is assumed."
        if not content:
            out["text_warning"] = ("The page had no act-content block; this is the whole "
                                   "page as text and may include site navigation.")
        if version == "enacted":
            out["version_warning"] = (
                "This is the Act AS ENACTED — later amendments are not applied. "
                "Do not present it as the current law without checking the "
                "Revised Acts collection."
            )
        end = start + len(window)
        if end < len(body):
            out["next_offset"] = end
            out["truncated"] = (
                "Characters %d-%d of %d. Continue with offset=%d, or read one "
                "section with get_section (the Companies Act 2014 has 1,448)."
                % (start, end, len(body), end)
            )
        if len(sections) > len(out["sections"]):
            out["sections_note"] = ("Index cut at %d of %d sections."
                                    % (len(out["sections"]), len(sections)))
        return out

    def get_section(self, year: int, number: int, section: str,
                    version: str = "enacted", max_chars: int = 30000) -> Dict[str, Any]:
        url = self.section_url(year, number, section, version)
        page = _fetch(url)
        body = _plain(page)
        title = _title(page)
        out = {
            "year": int(year),
            "number": int(number),
            "section": str(section),
            "version": version,
            "title": title,
            "url": url,
            "citation": "%s, s. %s" % (title or "Act", section),
            "length_chars": len(body),
            "text": body[:max_chars],
        }
        if version == "enacted":
            out["version_warning"] = (
                "Section as ENACTED — amendments not applied."
            )
        return out

    def list_year(self, year: int, probe_limit: int = 80,
                  miss_streak: int = 8, full_text: bool = False) -> List[Dict[str, Any]]:
        """Enumerate an Act year by walking numbers until the misses run on.

        The Statute Book has no year-index API — ``/eli/{year}/act/`` renders a
        page with no Act links — so the numbering itself is the index. Acts are
        numbered from 1 with occasional gaps, which is why this stops on a run of
        consecutive misses rather than the first one.

        ``full_text`` probes the print view, so ``text`` is the Act itself (what
        the crawler indexes); otherwise the lighter contents page is probed and
        ``text`` is the contents and long title.
        """
        found: List[Dict[str, Any]] = []
        misses = 0
        url_for = self.print_url if full_text else self.act_url
        for no in range(1, int(probe_limit) + 1):
            try:
                page = _fetch(url_for(year, no))
            except IeError:
                misses += 1
                if misses >= miss_streak:
                    break
                continue
            misses = 0
            title = _title(page)
            body = _plain(page)
            date, source = _dated(page)
            found.append({
                "year": int(year), "number": no, "title": title,
                # The enactment date as the page states it; "" when it does not.
                "date": date,
                "date_source": source,
                "url": self.act_url(year, no),
                "citation": "%s (No. %d of %d)" % (title or "Act", no, int(year)),
                "length_chars": len(body),
                "text": body,
            })
        return found
