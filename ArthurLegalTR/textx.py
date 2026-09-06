"""Text utilities for ArthurLegalTR: HTML → text, PDF → text, Turkish folding.

The only optional dependency in the whole server lives here. ``pypdf`` is pure
Python and extracts text from the PDFs that Rekabet Kurumu, EPDK, SPK, BTK and
the Sigorta Tahkim Komisyonu publish. Without it the server still runs; PDF
tools then return the URL and say plainly that text extraction is off, rather
than an empty string that looks like an empty decision.
"""

from __future__ import annotations

import html as _html
import io
import math
import re
from typing import Any, Dict, List, Optional, Tuple

try:  # optional, pure Python
    from pypdf import PdfReader  # type: ignore
    HAS_PYPDF = True
except Exception:  # noqa: BLE001
    PdfReader = None  # type: ignore
    HAS_PYPDF = False

__all__ = ["html_to_text", "pdf_to_text", "tr_lower", "tr_fold", "paginate",
           "clean_ws", "strip_tags", "HAS_PYPDF", "excerpt", "count_hits"]

_TR_LOWER = str.maketrans("İIÇĞÖŞÜÂÎÛ", "iıçğöşüâîû")
_TR_FOLD = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")


def tr_lower(text: str) -> str:
    """Lowercase with Turkish İ/I handling (``str.lower`` maps I→i, which is wrong)."""
    return text.translate(_TR_LOWER).lower()


def tr_fold(text: str) -> str:
    """Lowercase + strip Turkish diacritics, so ``Kürşat`` and ``kursat`` compare equal."""
    return tr_lower(text).translate(_TR_FOLD)


def clean_ws(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


_BLOCK = re.compile(r"</?(p|div|br|tr|li|h[1-6]|table|ul|ol|section|article|blockquote|pre)\b[^>]*>", re.I)
_CELL = re.compile(r"</?(td|th)\b[^>]*>", re.I)
_DROP = re.compile(r"<(script|style|noscript|head|svg)\b.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def strip_tags(fragment: str) -> str:
    return clean_ws(_html.unescape(_TAG.sub(" ", fragment)))


def html_to_text(html: str, keep_headings: bool = True) -> str:
    """HTML → readable plain text with paragraph breaks. No parser dependency.

    Not Markdown: headings become their own lines, tables become rows with
    ``|`` between cells, everything else is paragraphs. That is what a language
    model needs from a court decision; bold and italics are noise.
    """
    if not html:
        return ""
    text = _COMMENT.sub("", html)
    text = _DROP.sub(" ", text)
    if keep_headings:
        text = re.sub(r"<h([1-6])\b[^>]*>(.*?)</h\1>",
                      lambda m: "\n\n" + _TAG.sub("", m.group(2)).strip().upper() + "\n\n",
                      text, flags=re.I | re.S)
    text = _CELL.sub(" | ", text)
    text = _BLOCK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = _html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip(" |") for ln in text.split("\n")]
    text = "\n".join(lines)
    return clean_ws(text)


def pdf_to_text(data: bytes, max_pages: int = 0) -> Tuple[str, int]:
    """PDF bytes → (text, page_count). Empty text if pypdf is missing or the PDF is scanned."""
    if not HAS_PYPDF:
        return "", 0
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:  # noqa: BLE001 - corrupt PDF is a data problem, not a crash
        return "", 0
    pages = []
    n = len(reader.pages)
    limit = n if not max_pages else min(n, max_pages)
    for i in range(limit):
        try:
            pages.append(reader.pages[i].extract_text() or "")
        except Exception:  # noqa: BLE001
            pages.append("")
    text = "\n\n".join(p.strip() for p in pages)
    return clean_ws(text), n


def paginate(text: str, page: int, size: int = 6000) -> Dict[str, Any]:
    """Slice a long text into numbered chunks the model can page through."""
    text = text or ""
    total = max(1, math.ceil(len(text) / size)) if text else 0
    page = max(1, int(page or 1))
    start = (page - 1) * size
    chunk = text[start:start + size]
    return {"page": page, "total_pages": total, "chars": len(text),
            "has_more": page < total, "text": chunk}


def excerpt(body: str, needle: str, width: int = 220) -> str:
    """Short window around the first (folded) occurrence of ``needle``."""
    if not body:
        return ""
    fb, fn = tr_fold(body), tr_fold(needle)
    i = fb.find(fn) if fn else -1
    if i < 0:
        return body[: width * 2].strip() + ("…" if len(body) > width * 2 else "")
    s, e = max(0, i - width), min(len(body), i + len(needle) + width)
    return ("…" if s > 0 else "") + body[s:e].strip() + ("…" if e < len(body) else "")


def count_hits(body: str, needle: str) -> int:
    fb, fn = tr_fold(body), tr_fold(needle)
    return fb.count(fn) if fn else 0
