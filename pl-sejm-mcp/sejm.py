"""Client for Poland's Sejm ELI API — standard library only.

The API is genuinely good: ELI-compliant, no auth, and it carries the two things
Polish legal research turns on — ``status`` (``obowiązujący`` = in force,
``uchylony`` = repealed) and ``references``, the graph of what amended what.

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
from typing import Any, Dict, List, Optional

__version__ = "1.0.0"

API = "https://api.sejm.gov.pl/eli"
UA = "arthurlegal-pl-sejm-mcp/%s (+https://github.com/beerbottle90/arthurlegal-mcp)" % __version__

PUBLISHERS = {
    "DU": "Dziennik Ustaw — the Journal of Laws (primary legislation)",
    "MP": "Monitor Polski — the Official Gazette (resolutions, notices)",
}

# The publisher's own status vocabulary, echoed rather than translated away.
STATUS_IN_FORCE = "obowiązujący"


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


def _norm(item: Dict[str, Any]) -> Dict[str, Any]:
    """One act, with the citation and status a lawyer actually needs."""
    display = item.get("displayAddress") or ""
    status = item.get("status") or ""
    in_force = bool(item.get("inForce") is True or status == STATUS_IN_FORCE)
    return {
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
        "announcement_date": item.get("announcementDate", ""),
        "promulgation": item.get("promulgation", ""),
        "entry_into_force": item.get("entryIntoForce", ""),
        "valid_from": item.get("validFrom", ""),
        "eli": item.get("ELI", ""),
        "keywords": item.get("keywordsNames") or item.get("keywords") or [],
        "released_by": item.get("releasedBy", ""),
        "has_html": bool(item.get("textHTML")),
        "has_pdf": bool(item.get("textPDF")),
        "url": "https://api.sejm.gov.pl/eli/acts/%s/%s/%s"
               % (item.get("publisher"), item.get("year"), item.get("pos")),
        "citation": "%s — %s [%s]" % (display, item.get("title", ""), status or "status unknown"),
    }


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
        if publisher not in PUBLISHERS:
            raise SejmError("publisher must be DU or MP")
        data = _get("acts/%s/%d" % (publisher, int(year)))
        items = [_norm(i) for i in (data.get("items") or [])]
        total = data.get("count", len(items))
        window = items[int(offset): int(offset) + max(1, min(int(limit), 500))]
        return {"count": total, "returned": len(window), "results": window}

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
            "length_chars": len(raw),
            "text": raw[:max_chars],
        }
        if len(raw) > max_chars:
            out["truncated"] = "Truncated at %d of %d characters." % (max_chars, len(raw))
        return out
