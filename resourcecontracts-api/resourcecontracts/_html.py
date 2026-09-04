"""Minimal, dependency-free HTML-to-text conversion for contract text.

The ResourceContracts ``/contract/{id}/text`` endpoint returns OCR/full text as
JSON strings that still carry ``<br />`` line breaks and HTML character
references. We only need clean, readable text out of them, so this uses the
standard-library ``html.parser`` rather than pulling in BeautifulSoup or lxml —
keeping the whole package runnable on a stock Python 3.9 with no pip installs
(mirrors ``eqanun._html``).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags whose textual content we drop entirely.
_SKIP_TAGS = {"script", "style", "head", "noscript", "svg"}

# Block-level tags after which we force a line break so paragraphs, list items,
# table rows and headings do not run together into one wall of text.
_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "section", "article", "header", "footer",
    "ul", "ol", "blockquote", "hr", "pre",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def html_to_text(html: str) -> str:
    """Convert an HTML fragment to clean, readable plain text.

    Structure is approximated: block elements become line breaks, runs of
    whitespace inside a line are collapsed, and blank lines are limited to at
    most one in a row.
    """
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.get_text()

    # Collapse intra-line whitespace (incl. non-breaking spaces) but keep newlines.
    text = text.replace(" ", " ")
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in text.split("\n")]

    # Drop runs of blank lines down to a single separator.
    out: list[str] = []
    blank = False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()
