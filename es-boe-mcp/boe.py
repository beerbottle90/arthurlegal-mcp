"""Client for Spain's BOE open-data API — standard library only.

Why this server exists
----------------------
The consolidated-legislation API is fully public and free, but it refuses every
request that does not carry ``Accept: application/xml``:

    Accept: application/xml   -> 200  (Ley de Sociedades de Capital)
    Accept: application/json  -> 400  "No soportado ningún mime type"
    no Accept header          -> 400

An LLM's plain URL-fetch tool cannot set request headers, so the whole
consolidated corpus is invisible to it. A server can set the header, which is
the entire reason this wrapper exists.

The second reason: the list endpoint takes only ``limit`` and ``offset`` —
there is **no title or full-text search parameter** (``?query=`` returns 500,
``?titulo=`` returns 400). Search therefore has to be built locally, which is
what ``crawl.py`` and the shared ``retrieval`` index do.

Two traps in the consolidated document itself:

* ``/legislacion-consolidada/id/{id}`` is the WHOLE document — metadata,
  analysis, ELI RDF and the full text (3.4 MB for the Código Civil). Metadata
  lives at ``/metadatos`` and ``/analisis``; the text at ``/texto``, which in
  turn carries no metadata.
* ``/texto`` is versioned: each ``<bloque>`` holds every ``<version>`` it has
  had, each with its ``fecha_vigencia``. The law on a given day is one version
  per block, not all of them — joined, article 1 of the Código Civil reads
  twice, in its 1889 and its 1974 wording.
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

__version__ = "1.1.0"

BASE = "https://www.boe.es"
API = BASE + "/datosabiertos/api"
UA = "arthurlegal-es-boe-mcp/%s (+https://github.com/beerbottle90/arthurlegal-mcp)" % __version__

# The BOE ids this server accepts. Anything else is a caller mistake, and
# validating here keeps a malformed id from becoming a confusing upstream 404.
BOE_ID = re.compile(r"^BOE-[A-Z]-\d{4}-\d+$")
# A consolidated act keeps the id of the gazette that first published it. BOE's own
# are BOE-A-...; an act from a regional gazette carries that gazette's prefix and a
# lower-case series letter, DOGC-f-1997-90001, and the API rejects DOGC-F-...: 245
# of the 12,376 consolidated acts (2026-10-10), which get_act could not open.
_CONSOLIDATED_ID = re.compile(r"^([A-Za-z]{2,5})-([A-Za-z])-(\d{4})-(\d+)$")


def consolidated_id(raw: str) -> str:
    """The id as the API spells it, or "" when it is not a consolidated-act id."""
    m = _CONSOLIDATED_ID.match((raw or "").strip())
    if not m:
        return ""
    prefix = m.group(1).upper()
    letter = m.group(2).upper() if prefix == "BOE" else m.group(2).lower()
    return "%s-%s-%s-%s" % (prefix, letter, m.group(3), m.group(4))


class BoeError(Exception):
    """An upstream failure worth explaining to the caller."""


def _get(url: str, accept: str = "application/xml", timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", accept)          # <- the point of this whole module
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001 - diagnostics only
            pass
        if exc.code == 404:
            raise BoeError("Not found (404): %s" % url) from exc
        if exc.code == 400 and "mime" in body.lower():
            raise BoeError(
                "BOE rejected the Accept header — this endpoint only speaks "
                "application/xml. %s" % body
            ) from exc
        raise BoeError("HTTP %s from BOE: %s %s" % (exc.code, url, body)) from exc
    except urllib.error.URLError as exc:
        raise BoeError("Could not reach BOE: %s" % exc.reason) from exc


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _iso(compact: str) -> str:
    """BOE dates are ``YYYYMMDD`` (sometimes with a time suffix); make them ISO."""
    digits = re.sub(r"\D", "", compact or "")[:8]
    if len(digits) != 8:
        return ""
    return "%s-%s-%s" % (digits[0:4], digits[4:6], digits[6:8])


def _parse_root(raw: bytes) -> ET.Element:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise BoeError("BOE returned unparseable XML: %s" % exc) from exc
    status = root.find("./status/code")
    if status is not None and _text(status) not in ("200", ""):
        raise BoeError("BOE status %s: %s" % (_text(status), _text(root.find("./status/text"))))
    return root


def _compact_date(value: str) -> str:
    """``YYYY-MM-DD`` or ``YYYYMMDD`` -> ``YYYYMMDD``; "" means today."""
    if not (value or "").strip():
        return datetime.date.today().strftime("%Y%m%d")
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        raise BoeError("as_of must be YYYY-MM-DD or YYYYMMDD, got %r" % value)
    return digits


_DATE8 = re.compile(r"^\d{8}$")


def _version_on(bloque: ET.Element, day: str) -> Tuple[Optional[ET.Element], int]:
    """``(version in force on day, number of the block's other versions)``.

    The version in force is the one with the latest ``fecha_vigencia`` on or
    before ``day`` — by date, not position: the Código Civil lists art. 278's
    1978 version before its 1943 one. A version without a date (art. 56 has
    one, deferred and then superseded) never wins over a dated one. A block
    with no dated version at all keeps its last version; a block whose every
    dated version starts after ``day`` was not yet law and yields None.
    """
    versions = bloque.findall("version")
    dated = [(v.get("fecha_vigencia"), i, v) for i, v in enumerate(versions)
             if _DATE8.match(v.get("fecha_vigencia") or "")]
    live = [d for d in dated if d[0] <= day]
    if live:
        return max(live, key=lambda d: (d[0], d[1]))[2], len(versions) - 1
    if not dated and versions:
        return versions[-1], len(versions) - 1
    return None, 0


def _lines(el: ET.Element) -> List[str]:
    """One line per paragraph; table rows as cells joined by " | "."""
    out: List[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == "table":
            for tr in node.iter("tr"):
                cells = [re.sub(r"\s+", " ", "".join(td.itertext())).strip()
                         for td in tr if td.tag in ("td", "th")]
                if any(cells):
                    out.append(" | ".join(cells))
            return
        if node.tag == "p":
            line = re.sub(r"\s+", " ", "".join(node.itertext())).strip()
            if line:
                out.append(line)
            return
        for child in node:
            walk(child)

    walk(el)
    if not out:
        flat = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
        if flat:
            out.append(flat)
    return out


class BoeClient:
    """Thin, honest wrapper. Every method names the endpoint it calls."""

    # -- consolidated legislation ---------------------------------------- #
    def list_consolidated(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """``GET /legislacion-consolidada?limit&offset``.

        Metadata only, no search parameters — pagination is the *only* way
        through the corpus, which is why the crawler exists.
        """
        limit = max(1, min(int(limit), 1000))     # 1000 verified as the working ceiling
        url = "%s/legislacion-consolidada?limit=%d&offset=%d" % (API, limit, int(offset))
        root = _parse_root(_get(url))
        out = []
        for item in root.findall("./data/item"):
            ident = _text(item.find("identificador"))
            # 'S' means the act's validity is spent — BOE's own repeal signal.
            # Carried through to the index so search results can say so.
            spent = _text(item.find("vigencia_agotada")).upper() == "S"
            out.append(
                {
                    "id": ident,
                    "title": _text(item.find("titulo")),
                    "rango": _text(item.find("rango")),
                    "departamento": _text(item.find("departamento")),
                    "ambito": _text(item.find("ambito")),
                    "numero_oficial": _text(item.find("numero_oficial")),
                    "date": _iso(_text(item.find("fecha_disposicion"))),
                    "published": _iso(_text(item.find("fecha_publicacion"))),
                    "updated": _text(item.find("fecha_actualizacion")),
                    "fecha_vigencia": _iso(_text(item.find("fecha_vigencia"))),
                    "vigencia_agotada": spent,
                    "status": "vigencia agotada" if spent else "vigente",
                    "estado_consolidacion": _text(item.find("estado_consolidacion")),
                    "eli": _text(item.find("url_eli")),
                    "url": _text(item.find("url_html_consolidada"))
                           or "%s/buscar/act.php?id=%s" % (BASE, ident),
                }
            )
        return out

    def _part(self, boe_id: str, part: str) -> ET.Element:
        """``GET /legislacion-consolidada/id/{id}/{part}`` -> its ``<data>``."""
        ident = consolidated_id(boe_id)
        if not ident:
            raise BoeError("Malformed BOE id %r — expected e.g. BOE-A-2010-10544 "
                           "(or a regional gazette's, e.g. DOGC-f-1997-90001)" % boe_id)
        boe_id = ident
        if part not in ("metadatos", "analisis", "texto"):
            raise BoeError("part must be one of: metadatos, analisis, texto")
        url = "%s/legislacion-consolidada/id/%s/%s" % (API, boe_id, part)
        data = _parse_root(_get(url)).find("./data")
        if data is None:
            raise BoeError("No <data> in BOE response for %s" % boe_id)
        return data

    def metadata(self, boe_id: str) -> Dict[str, Any]:
        """``/metadatos`` — the act's header and BOE's own status flags."""
        boe_id = consolidated_id(boe_id) or (boe_id or "").strip()
        data = self._part(boe_id, "metadatos")
        meta = data.find("metadatos") if data.find("metadatos") is not None else data
        title = _text(meta.find("titulo"))
        return {
            "id": boe_id,
            "title": title,
            "rango": _text(meta.find("rango")),
            "departamento": _text(meta.find("departamento")),
            "ambito": _text(meta.find("ambito")),
            "date": _iso(_text(meta.find("fecha_disposicion"))),
            "published": _iso(_text(meta.find("fecha_publicacion"))),
            "diario": _text(meta.find("diario")),
            "diario_numero": _text(meta.find("diario_numero")),
            # These are the status discipline: BOE says outright whether the act
            # has been repealed or annulled, or its validity is spent. Never
            # report a text as being in force without echoing them.
            "estatus_derogacion": _text(meta.find("estatus_derogacion")),
            "estatus_anulacion": _text(meta.find("estatus_anulacion")),
            "vigencia_agotada": _text(meta.find("vigencia_agotada")).upper() == "S",
            "fecha_vigencia": _iso(_text(meta.find("fecha_vigencia"))),
            "estado_consolidacion": _text(meta.find("estado_consolidacion")),
            "updated": _text(meta.find("fecha_actualizacion")),
            "eli": _text(meta.find("url_eli")),
            "url": "%s/buscar/act.php?id=%s" % (BASE, boe_id),
            "citation": "%s (%s)" % (title, boe_id),
        }

    def get_act(self, boe_id: str) -> Dict[str, Any]:
        """Header, status flags and subjects (``/metadatos`` + ``/analisis``). No text."""
        result = self.metadata(boe_id)
        materias = [_text(m) for m in self._part(result["id"], "analisis").findall(".//materias/materia")]
        if materias:
            result["materias"] = materias
        return result

    def get_consolidated(self, boe_id: str, part: str = "") -> Dict[str, Any]:
        """Kept for callers of the old name: ``texto`` is get_text, else get_act.

        It used to fetch the whole document and return every version of every
        block as one string; neither is what a caller of this name wants.
        """
        part = (part or "").strip("/")
        if part and part not in ("metadatos", "analisis", "texto"):
            raise BoeError("part must be one of: metadatos, analisis, texto")
        if part == "texto":
            return self.get_text(boe_id)
        return self.metadata(boe_id) if part == "metadatos" else self.get_act(boe_id)

    def get_text(self, boe_id: str, max_chars: int = 60000, offset: int = 0,
                 as_of: str = "") -> Dict[str, Any]:
        """The consolidated text in force on ``as_of`` (default today), with its header.

        One version per block — the one in force that day. Long acts come in
        windows of ``max_chars``; ``next_offset`` says where the next starts.
        """
        day = _compact_date(as_of)
        doc = self.metadata(boe_id)
        texto = self._part(doc["id"], "texto").find("texto")
        if texto is None:
            raise BoeError("No <texto> in BOE response for %s" % doc["id"])
        blocks: List[str] = []
        omitted = not_yet = 0
        for bloque in texto.findall("bloque"):
            version, others = _version_on(bloque, day)
            if version is None:
                not_yet += 1
                continue
            omitted += others
            lines = _lines(version)
            if lines:
                blocks.append("\n".join(lines))
        body = "\n\n".join(blocks)
        start = max(0, int(offset))
        window = body[start:start + max(1, int(max_chars))]
        doc.update({
            "consolidated": True,
            "as_of": _iso(day),
            "blocks": len(blocks),
            "length_chars": len(body),
            "offset": start,
            "text": window,
            "version_note": (
                "Each block in the version in force on %s; %d earlier or later "
                "versions of those blocks are not included%s. Pass as_of for the "
                "text on another date."
                % (_iso(day), omitted,
                   "" if not not_yet else "; %d blocks were not yet in force" % not_yet)),
        })
        end = start + len(window)
        if end < len(body):
            doc["next_offset"] = end
            doc["truncated"] = ("Characters %d-%d of %d. Continue with offset=%d."
                                % (start, end, len(body), end))
        return doc

    # -- daily gazette ---------------------------------------------------- #
    def daily_summary(self, date_yyyymmdd: str) -> Dict[str, Any]:
        """``GET /boe/sumario/{YYYYMMDD}`` — what was published that day."""
        digits = re.sub(r"\D", "", date_yyyymmdd or "")
        if len(digits) != 8:
            raise BoeError("date must be YYYYMMDD or YYYY-MM-DD, got %r" % date_yyyymmdd)
        root = _parse_root(_get("%s/boe/sumario/%s" % (API, digits)))
        items = []
        for it in root.findall(".//item"):
            ident = _text(it.find("identificador"))
            if not ident:
                continue
            items.append(
                {
                    "id": ident,
                    "title": _text(it.find("titulo")),
                    "url": "%s/diario_boe/xml.php?id=%s" % (BASE, ident),
                }
            )
        return {"date": _iso(digits), "count": len(items), "items": items}

    # -- single published document ---------------------------------------- #
    def get_document(self, boe_id: str, max_chars: int = 60000) -> Dict[str, Any]:
        """``GET /diario_boe/xml.php?id={id}`` — the act **as published**.

        This is *not* the consolidated text: later amendments are not applied.
        The returned ``consolidated: False`` flag exists so a caller cannot
        mistake one for the other.
        """
        boe_id = (boe_id or "").strip().upper()
        if not BOE_ID.match(boe_id):
            raise BoeError("Malformed BOE id %r" % boe_id)
        raw = _get("%s/diario_boe/xml.php?id=%s" % (BASE, urllib.parse.quote(boe_id)))
        root = _parse_root(raw)
        meta = root.find("./metadatos")
        body = _text(root.find("./texto"))
        out = {
            "id": boe_id,
            "consolidated": False,
            "warning": "Text as published in the BOE. Later amendments are NOT "
                       "applied — use get_consolidated_act for the in-force text.",
            "title": _text(meta.find("titulo")) if meta is not None else "",
            "departamento": _text(meta.find("departamento")) if meta is not None else "",
            "rango": _text(meta.find("rango")) if meta is not None else "",
            "date": _iso(_text(meta.find("fecha_disposicion"))) if meta is not None else "",
            "url": "%s/diario_boe/xml.php?id=%s" % (BASE, boe_id),
            "length_chars": len(body),
            "text": body[:max_chars],
        }
        if len(body) > max_chars:
            out["truncated"] = "Truncated at %d of %d characters." % (max_chars, len(body))
        return out
