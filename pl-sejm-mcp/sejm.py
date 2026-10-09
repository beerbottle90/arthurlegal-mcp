"""Client for Poland's Sejm ELI API — standard library only.

The API is genuinely good: ELI-compliant, no auth, and it carries the two things
Polish legal research turns on — whether an act is in force, and
``references``, the graph of what amended what.

In force is the API's ``inForce`` flag, not the ``status`` label. The flag is
now text (``IN_FORCE`` / ``NOT_IN_FORCE`` / ``UNKNOWN``, the StatusInForce enum
of the published OpenAPI spec); it used to be a JSON boolean. The label is the
publisher's own vocabulary and often describes consolidation rather than
validity: the Civil Code is ``akt posiada tekst jednolity`` ("has a
consolidated text") and IN_FORCE. Reading the label as the answer is how the
Code came out "not in force".

What it does **not** do is search bodies: ``/eli/acts/search`` matches titles
only. A phrase inside article 49 of the Energy Law is unreachable by title
search, which is why this server also keeps a local index (``crawl.py``).

Responses are gzip-encoded, and ``urllib`` does not decompress automatically —
handled in ``_get``.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

__version__ = "1.1.0"

API = "https://api.sejm.gov.pl/eli"
UA = "arthurlegal-pl-sejm-mcp/%s (+https://github.com/beerbottle90/arthurlegal-mcp)" % __version__

PUBLISHERS = {
    "DU": "Dziennik Ustaw — the Journal of Laws (primary legislation)",
    "MP": "Monitor Polski — the Official Gazette (resolutions, notices)",
}

# The publisher's own status vocabulary, echoed rather than translated away.
STATUS_IN_FORCE = "obowiązujący"

# The API's in-force flag (StatusInForce). Anything else — UNKNOWN, or a value
# added upstream later — is reported as unknown, never guessed.
IN_FORCE_FLAGS = {"IN_FORCE": True, "NOT_IN_FORCE": False}

# Fallback for responses that carry a label but no flag: the /references
# headers, and index rows written before the flag was recorded. In a sample of
# 1,224 acts (2026-10-09) every act with one of the first three labels was
# flagged IN_FORCE, and every act with a label from the second set was flagged
# NOT_IN_FORCE; the second set's other labels say repeal or expiry outright.
# "bez statusu" went both ways (27 / 77) and "akt jednorazowy" is a one-off
# act, so those and any unlisted label stay unknown.
STATUS_LABELS_IN_FORCE = frozenset({
    "obowiązujący",
    "akt posiada tekst jednolity",
    "akt objęty tekstem jednolitym",
})
STATUS_LABELS_NOT_IN_FORCE = frozenset({
    "uchylony",
    "uchylony wykazem",
    "uznany za uchylony",
    "wygaśnięcie aktu",
    "nieobowiązujący - przyczyna nieustalona",
    "nieobowiązujący - uchylona podstawa prawna",
    "brak mocy prawnej",
})


class SejmError(Exception):
    """An upstream failure worth explaining to the caller."""


def _get(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 45) -> Any:
    url = "%s/%s" % (API, path.lstrip("/"))
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "", [])}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Encoding", "gzip")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SejmError("Not found (404): %s" % url) from exc
        raise SejmError("HTTP %s from the Sejm API: %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise SejmError("Could not reach api.sejm.gov.pl: %s" % exc.reason) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise SejmError("Sejm API returned unparseable JSON: %s" % exc) from exc


def in_force_from_status(status: str) -> Optional[bool]:
    """The label-only reading, for data that carries no ``inForce`` flag."""
    label = (status or "").strip()
    if label in STATUS_LABELS_IN_FORCE:
        return True
    if label in STATUS_LABELS_NOT_IN_FORCE:
        return False
    return None


def _in_force(item: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    """``(in_force, warning)``. ``in_force`` is None when the API does not say.

    The flag decides whenever it is present. The label is consulted only when
    the flag is absent, and only the labels in the two sets above count.
    """
    raw = item.get("inForce")
    if isinstance(raw, bool):                    # the API's older encoding
        return raw, ""
    if isinstance(raw, str) and raw.strip():
        flag = IN_FORCE_FLAGS.get(raw.strip().upper())
        if flag is not None:
            return flag, ""
        return None, ("The Sejm API reports inForce=%r, which this server does not "
                      "interpret. In-force status is unknown — check the act on "
                      "isap.sejm.gov.pl before relying on it." % raw)
    status = item.get("status") or ""
    flag = in_force_from_status(status)
    if flag is None:
        return None, ("No inForce flag in this response and the status label %r does "
                      "not settle it. In-force status is unknown — call get_act."
                      % (status or "(none)"))
    return flag, ""


def _norm(item: Dict[str, Any]) -> Dict[str, Any]:
    """One act, with the citation and status a lawyer actually needs."""
    display = item.get("displayAddress") or ""
    status = item.get("status") or ""
    in_force, warning = _in_force(item)
    raw_flag = item.get("inForce")
    if isinstance(raw_flag, bool):
        raw_flag = "IN_FORCE" if raw_flag else "NOT_IN_FORCE"
    # The bracket carries the publisher's label AND the in-force flag: the label
    # alone ("akt posiada tekst jednolity") does not tell a reader the act is law.
    if in_force is None:
        force_note = "in force: unknown"
    else:
        force_note = raw_flag or ("IN_FORCE" if in_force else "NOT_IN_FORCE")
    keywords: List[str] = []
    for name in (item.get("keywords") or []) + (item.get("keywordsNames") or []):
        if name and name not in keywords:
            keywords.append(name)
    out = {
        "address": item.get("address", ""),           # WDU20240001984
        "display_address": display,                    # Dz.U. 2024 poz. 1984
        "title": item.get("title", ""),
        "type": item.get("type", ""),
        "publisher": item.get("publisher", ""),
        "year": item.get("year"),
        "pos": item.get("pos"),
        # Status is part of the citation for Polish law, not a footnote.
        "status": status,
        "in_force": in_force,
        "in_force_api": raw_flag if isinstance(raw_flag, str) else None,
        "announcement_date": item.get("announcementDate", ""),
        "promulgation": item.get("promulgation", ""),
        "entry_into_force": item.get("entryIntoForce", ""),
        "valid_from": item.get("validFrom", ""),
        "eli": item.get("ELI", ""),
        "keywords": keywords,
        "released_by": item.get("releasedBy", ""),
        "has_html": bool(item.get("textHTML")),
        "has_pdf": bool(item.get("textPDF")),
        "url": "https://api.sejm.gov.pl/eli/acts/%s/%s/%s"
               % (item.get("publisher"), item.get("year"), item.get("pos")),
        "citation": "%s — %s [%s | %s]" % (display, item.get("title", ""),
                                          status or "status unknown", force_note),
    }
    if warning:
        out["in_force_warning"] = warning
    return out


class SejmClient:
    def search(self, title: str = "", publisher: str = "", year: Optional[int] = None,
               act_type: str = "", in_force_only: bool = False,
               limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        """``GET /eli/acts/search`` — **title matching only**, never the body."""
        if not any([title, publisher, year, act_type]):
            raise SejmError("Provide at least one of: title, publisher, year, act_type.")
        params: Dict[str, Any] = {
            "title": title, "publisher": publisher, "year": year,
            "type": act_type, "limit": max(1, min(int(limit), 100)), "offset": int(offset),
        }
        if in_force_only:
            params["inForce"] = 1
        data = _get("acts/search", params)
        items = [_norm(i) for i in (data.get("items") or [])]
        return {
            "count": data.get("count", len(items)),
            "scope": "TITLE MATCH ONLY — the Sejm search endpoint does not read "
                     "act bodies. Use search_indexed for body/keyword search.",
            "results": items,
        }

    def list_year(self, publisher: str, year: int, limit: int = 100,
                  offset: int = 0) -> Dict[str, Any]:
        """Acts published in a year, newest position first.

        Served by ``/acts/search?publisher&year`` rather than ``/acts/{p}/{y}``:
        the year listing omits ``inForce``, leaving only the status label, and
        for 111 of the 122 "bez statusu" acts of 1964 the flag says IN_FORCE.
        Same order, upstream paging instead of slicing a full-year download.
        """
        if publisher not in PUBLISHERS:
            raise SejmError("publisher must be DU or MP")
        data = _get("acts/search", {
            "publisher": publisher, "year": int(year),
            "limit": max(1, min(int(limit), 500)), "offset": max(0, int(offset)),
        })
        items = [_norm(i) for i in (data.get("items") or [])]
        total = data.get("totalCount", data.get("count", len(items)))
        return {"count": total, "returned": len(items), "results": items}

    def get_act(self, publisher: str, year: int, pos: int) -> Dict[str, Any]:
        if publisher not in PUBLISHERS:
            raise SejmError("publisher must be DU or MP")
        data = _get("acts/%s/%d/%d" % (publisher, int(year), int(pos)))
        out = _norm(data)
        # references maps relationship -> list of related acts: which act amended
        # this one, which it repealed, and so on.
        refs = data.get("references") or {}
        if refs:
            out["references"] = {
                k: [
                    {"display_address": v.get("displayAddress", ""), "id": v.get("id", ""),
                     "art": v.get("art", "")}
                    for v in (vals if isinstance(vals, list) else [vals])
                    if isinstance(v, dict)
                ]
                for k, vals in refs.items()
            }
            out["references_note"] = (
                "`references` is the amendment graph. Check it before treating "
                "this text as current: an act can be `obowiązujący` and still "
                "have been amended many times."
            )
        if data.get("directives"):
            out["eu_directives"] = data["directives"]
        if data.get("previousTitle"):
            out["previous_titles"] = data["previousTitle"]
        return out

    def get_text(self, publisher: str, year: int, pos: int, fmt: str = "html",
                 max_chars: int = 60000) -> Dict[str, Any]:
        """``/text.html`` or ``/text.pdf``.

        Checks the ``textHTML`` flag first: many acts are PDF-only, and asking
        for HTML then would return nothing useful with no explanation.
        """
        if fmt not in ("html", "pdf"):
            raise SejmError("fmt must be 'html' or 'pdf'")
        meta = self.get_act(publisher, year, pos)
        if fmt == "html" and not meta["has_html"]:
            raise SejmError(
                "%s has no HTML text (textHTML=false); only PDF is published. "
                "Use fmt='pdf' and read it from the URL." % meta["display_address"]
            )
        url = "%s/acts/%s/%d/%d/text.%s" % (API, publisher, int(year), int(pos), fmt)
        if fmt == "pdf":
            return {
                "display_address": meta["display_address"], "status": meta["status"],
                "pdf_url": url,
                "note": "PDF is binary — this server returns the URL, not the bytes.",
            }
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", UA)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise SejmError("HTTP %s fetching %s" % (exc.code, url)) from exc
        except urllib.error.URLError as exc:
            raise SejmError("Could not reach api.sejm.gov.pl: %s" % exc.reason) from exc
        out = {
            "display_address": meta["display_address"],
            "title": meta["title"],
            "status": meta["status"],
            "in_force": meta["in_force"],
            "citation": meta["citation"],
            "url": url,
            **({"in_force_warning": meta["in_force_warning"]}
               if meta.get("in_force_warning") else {}),
            "length_chars": len(raw),
            "text": raw[:max_chars],
        }
        if len(raw) > max_chars:
            out["truncated"] = "Truncated at %d of %d characters." % (max_chars, len(raw))
        return out
