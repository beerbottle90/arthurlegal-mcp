"""Client for the Irish Statute Book — standard library only.

The Statute Book publishes clean, server-rendered ELI documents:

    /eli/{year}/act/{no}/enacted/en/html            whole Act
    /eli/{year}/act/{no}/section/{n}/enacted/en/html  one section

Both verified to return real text (Companies Act 2014 = 107,652 characters of
body text; its section 1 = 3,512). What it does **not** publish is a search
endpoint — ``/search`` and ``/searchresults.html`` both 404 — so search is served
from a local index built by ``crawl.py``.

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

__version__ = "1.0.0"

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


# Site chrome that appears on every page, in English and Irish. Left in, it
# would be the most common phrase in the corpus and would pollute BM25 scoring.
_CHROME = re.compile(
    r"(Skip to content|Disclaimer|Feedback|Helpdesk|Gaeilge|"
    r"Léim go dtí an t-ábhar|Séanadh|Aiseolas|Deasc chabhrach|"
    r"Baile|Home|Irish Statute Book|Print|Share)",
    re.I,
)


def _plain(page: str) -> str:
    text = _SCRIPT.sub(" ", page)
    text = _TAG.sub(" ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    # Strip the leading navigation run, not every occurrence: the words can also
    # appear legitimately inside a section's text.
    head, tail = text[:400], text[400:]
    head = _CHROME.sub(" ", head)
    return re.sub(r"\s+", " ", head + tail).strip()


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
                max_chars: int = 60000) -> Dict[str, Any]:
        url = self.act_url(year, number, version)
        page = _fetch(url)
        body = _plain(page)
        title = _title(page)
        out: Dict[str, Any] = {
            "year": int(year),
            "number": int(number),
            "version": version,
            "title": title,
            "date_enacted": _enacted_date(page),
            "url": url,
            "citation": "%s (No. %d of %d)" % (title or "Act", int(number), int(year)),
            "length_chars": len(body),
            "text": body[:max_chars],
        }
        if not out["date_enacted"]:
            out["date_note"] = "The page states no enactment date; none is assumed."
        if version == "enacted":
            out["version_warning"] = (
                "This is the Act AS ENACTED — later amendments are not applied. "
                "Do not present it as the current law without checking the "
                "Revised Acts collection."
            )
        if len(body) > max_chars:
            out["truncated"] = (
                "Truncated at %d of %d characters. Irish Acts are long (the "
                "Companies Act 2014 has 1,448 sections) — prefer get_section."
                % (max_chars, len(body))
            )
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
                  miss_streak: int = 8) -> List[Dict[str, Any]]:
        """Enumerate an Act year by walking numbers until the misses run on.

        The Statute Book has no year-index API — ``/eli/{year}/act/`` renders a
        page with no Act links — so the numbering itself is the index. Acts are
        numbered from 1 with occasional gaps, which is why this stops on a run of
        consecutive misses rather than the first one.
        """
        found: List[Dict[str, Any]] = []
        misses = 0
        for no in range(1, int(probe_limit) + 1):
            try:
                page = _fetch(self.act_url(year, no))
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
