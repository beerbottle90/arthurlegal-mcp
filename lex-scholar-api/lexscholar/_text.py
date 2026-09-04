"""Dependency-free text extraction for the formats these sources actually serve.

Three shapes show up across the eight adapters:

- **HTML** landing pages and article bodies;
- **JATS XML** (SciELO serves ``?format=xml`` with a real ``<body>``);
- **OpenAlex inverted index**, where the abstract is stored as
  ``{word: [positions]}`` and has to be rebuilt into a sentence.

All of it is handled with ``html.parser`` / ``ElementTree`` from the standard
library — no BeautifulSoup, no lxml (mirrors ``eqanun._html``).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

# Tags whose textual content we drop entirely.
_SKIP_TAGS = {"script", "style", "head", "noscript", "svg"}

# Block-level tags after which we force a line break, so paragraphs, list items
# and table rows do not run together into one wall of text.
_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "section", "article", "header", "footer",
    "ul", "ol", "blockquote", "hr", "pre",
    # JATS equivalents
    "sec", "title", "abstract", "body", "front", "back", "ref", "label",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip > 0:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def markup_to_text(markup: str) -> str:
    """Convert HTML or JATS XML to clean, readable plain text."""
    parser = _TextExtractor()
    parser.feed(markup)
    out = parser.text().replace(" ", " ")
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in out.split("\n")]

    kept: List[str] = []
    blank = False
    for ln in lines:
        if ln:
            kept.append(ln)
            blank = False
        elif not blank:
            kept.append("")
            blank = True
    return "\n".join(kept).strip()


def jats_body_text(xml: str) -> str:
    """Extract just the ``<body>`` of a JATS article, falling back to the whole doc.

    SciELO's ``?format=xml`` returns front matter, body and a long reference
    list; the body is the part worth reading, so slice it out before stripping
    tags to avoid dragging in hundreds of citations.
    """
    start = xml.find("<body")
    if start == -1:
        return markup_to_text(xml)
    end = xml.find("</body>", start)
    body = xml[start:end + 7] if end != -1 else xml[start:]
    return markup_to_text(body)


def abstract_from_inverted_index(index: Optional[Dict[str, List[int]]]) -> Optional[str]:
    """Rebuild an OpenAlex abstract from its inverted index.

    OpenAlex ships abstracts as ``{"word": [positions...]}`` for licensing
    reasons; reversing it gives back the original sentence order.
    """
    if not index:
        return None
    positions: Dict[int, str] = {}
    for word, spots in index.items():
        for spot in spots:
            positions[spot] = word
    if not positions:
        return None
    return " ".join(positions[k] for k in sorted(positions))


def strip_tags(value: Any) -> Optional[str]:
    """Collapse a possibly-markup string to plain text (used on titles)."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if "<" in text:
        text = markup_to_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
