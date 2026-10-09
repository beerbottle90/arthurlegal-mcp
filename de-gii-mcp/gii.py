"""gesetze-im-internet.de: the consolidated text of German federal law, norm by norm.

Why this sits next to NeuRIS. NeuRIS (rechtsinformationen.bund.de) is the
successor, but its test-phase dataset holds about 5,500 acts and lacks the core
codes: BGB, HGB, StGB, ZPO, StPO, AO, UrhG, GWB and InsO all return 0 for
``/v1/legislation?abbreviation=`` (checked 2026-10-09). gesetze-im-internet.de,
run by the Federal Ministry of Justice with juris, carries "nahezu das gesamte
aktuelle Bundesrecht", one XML file per act, free to reuse. NeuRIS is to replace
it at a date not yet set.

Two public files, no key:

    /gii-toc.xml        every act: <item><title>...</title><link>.../<slug>/xml.zip</link></item>
    /<slug>/xml.zip     one act (gii-norm.dtd): a <norm> per paragraph or article,
                        <metadaten><jurabk/><enbez/><titel/></metadaten> and
                        <textdaten><text><Content><P>...</P></Content></text></textdaten>

The text is the consolidated version, not the authentic one: only the
Bundesgesetzblatt is authentic. Every result says so and links the norm's own
page, https://www.gesetze-im-internet.de/<slug>/__<n>.html (art_<n>.html for
articles).
"""

from __future__ import annotations

import html
import io
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

__version__ = "0.1.0"

BASE = "https://www.gesetze-im-internet.de"
TOC_URL = BASE + "/gii-toc.xml"
_UA = "arthurlegal-mcp/de-gii-%s (+https://github.com/beerbottle90/arthurlegal-mcp)" % __version__
_TOC_TTL = 24 * 3600
_LAW_CACHE = 6        # parsed acts kept in memory; the BGB alone is several MB of XML
_PAGE_CHARS = 20000

AUTHENTICITY = ("Nicht amtliche konsolidierte Fassung (gesetze-im-internet.de, BMJ und juris). "
                "Amtlich ist allein die Verkündung im Bundesgesetzblatt (recht.bund.de).")

# Abbreviations whose page name is not simply the abbreviation in lower case. The
# fetched act's own <jurabk>/<amtabk> is reported with every result, and a guessed
# page name is checked against it, so a wrong guess fails loudly instead of
# answering with another act.
SLUGS = {
    "AO": "ao_1977", "BDSG": "bdsg_2018", "UWG": "uwg_2004", "VVG": "vvg_2008",
    "USTG": "ustg_1980", "KSTG": "kstg_1977", "ENWG": "enwg_2005", "TKG": "tkg_2021",
    "KWG": "kredwg", "BAUGB": "bbaug", "WEG": "woeigg", "EGBGB": "bgbeg",
    "SGB I": "sgb_1", "SGB II": "sgb_2", "SGB III": "sgb_3", "SGB IV": "sgb_4",
    "SGB V": "sgb_5", "SGB VI": "sgb_6", "SGB VII": "sgb_7", "SGB VIII": "sgb_8",
    "SGB IX": "sgb_9_2018", "SGB X": "sgb_10", "SGB XI": "sgb_11", "SGB XII": "sgb_12",
}


class GiiError(Exception):
    def __init__(self, message: str, status: Optional[int] = None,
                 candidates: Optional[List[Any]] = None) -> None:
        super().__init__(message)
        self.status = status
        self.candidates = candidates or []


_lock = threading.Lock()
_laws: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_toc: Dict[str, Any] = {"at": 0.0, "items": []}


def _fetch(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise GiiError("HTTP %d from %s" % (exc.code, url), status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GiiError("gesetze-im-internet.de unreachable (%s)" % getattr(exc, "reason", exc)) from exc


# --------------------------------------------------------------------------- #
# XML
# --------------------------------------------------------------------------- #
_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*>", re.I)
_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def _xml_safe(raw: str) -> str:
    """Drop the DOCTYPE and turn HTML entities into characters.

    The files name gii-norm.dtd, which ElementTree does not load, so an entity it
    defines (&nbsp; and friends) would otherwise stop the parse.
    """
    raw = _DOCTYPE.sub("", raw, count=1)

    def entity(m: "re.Match[str]") -> str:
        if m.group(1) in _XML_ENTITIES:
            return m.group(0)
        char = html.unescape(m.group(0))
        return char if char != m.group(0) else ""

    return _ENTITY.sub(entity, raw)


def _render(el: ET.Element) -> str:
    """Plain text with paragraph breaks; enumerations stay one item per line."""
    out = el.text or ""
    for child in el:
        tag = child.tag
        if tag == "BR":
            out += "\n"
        elif tag in ("P", "LA", "DL", "row", "Title", "Subtitle"):
            out += "\n" + _render(child).strip() + "\n"
        elif tag == "DT":
            out += "\n" + _render(child).strip() + " "
        elif tag == "DD":
            out += _render(child).strip()
        elif tag == "entry":
            out += _render(child).strip() + " | "
        elif tag in ("FnR", "FnArea", "Footnotes"):
            pass  # footnote anchors and blocks; the norm text reads without them
        else:
            out += _render(child)
        out += child.tail or ""
    return out


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _child_text(el: Optional[ET.Element], tag: str) -> str:
    if el is None:
        return ""
    found = el.find(tag)
    return _tidy("".join(found.itertext())) if found is not None else ""


def _parse_zip(data: bytes, slug: str) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
            if not name:
                raise GiiError("no XML file in %s/xml.zip" % slug)
            raw = zf.read(name).decode("utf-8", "replace")
    except zipfile.BadZipFile as exc:
        raise GiiError("%s/xml.zip is not a ZIP file" % slug) from exc
    try:
        root = ET.fromstring(_xml_safe(raw))
    except ET.ParseError as exc:
        raise GiiError("cannot parse %s: %s" % (slug, exc)) from exc
    norms = root.findall("norm")
    if not norms:
        raise GiiError("no <norm> in %s" % slug)
    head = norms[0].find("metadaten")
    stand = []
    for sa in (head.findall("standangabe") if head is not None else []):
        kind, note = _child_text(sa, "standtyp"), _child_text(sa, "standkommentar")
        if note:
            stand.append("%s: %s" % (kind, note) if kind else note)
    fundstelle = ""
    if head is not None and head.find("fundstelle") is not None:
        fs = head.find("fundstelle")
        fundstelle = " ".join(p for p in (_child_text(fs, "periodikum"), _child_text(fs, "zitstelle")) if p)
    act: Dict[str, Any] = {
        "slug": slug,
        "jurabk": _child_text(head, "jurabk"),
        "amtabk": _child_text(head, "amtabk"),
        "title": _child_text(head, "langue") or _child_text(head, "kurzue"),
        "ausfertigung": _child_text(head, "ausfertigung-datum"),
        "fundstelle": fundstelle,
        "stand": stand,
        "norms": [],
    }
    for n in norms:
        md = n.find("metadaten")
        enbez = _child_text(md, "enbez")
        if not enbez:
            continue  # headings of the outline carry no norm number
        content = n.find("textdaten/text/Content")
        act["norms"].append({
            "enbez": enbez,
            "titel": _child_text(md, "titel"),
            "text": _tidy(_render(content)) if content is not None else "",
        })
    return act


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #
def toc() -> List[Dict[str, str]]:
    """Every act on the site as {title, slug}; cached for a day."""
    with _lock:
        if _toc["items"] and time.time() - _toc["at"] < _TOC_TTL:
            return _toc["items"]
    root = ET.fromstring(_xml_safe(_fetch(TOC_URL).decode("utf-8", "replace")))
    items = []
    for it in root.iter("item"):
        title = _tidy(it.findtext("title") or "")
        m = re.search(r"/([^/]+)/xml\.zip$", (it.findtext("link") or "").strip())
        if title and m:
            items.append({"title": title, "slug": m.group(1)})
    with _lock:
        _toc.update(at=time.time(), items=items)
    return items


def search_laws(query: str, limit: int = 10) -> List[Dict[str, str]]:
    """Acts whose title (or page name) matches ``query``; shorter titles first."""
    q = query.strip().casefold()
    words = [w for w in re.split(r"\W+", q) if w]
    scored = []
    for it in toc():
        title = it["title"].casefold()
        if q in (it["slug"], title):
            score = 3.0
        elif title.startswith(q):
            score = 2.0
        elif words and all(w in title for w in words):
            score = 1.0
        else:
            continue
        scored.append((score - len(title) / 10000.0, it))
    scored.sort(key=lambda pair: -pair[0])
    return [dict(it, url="%s/%s/index.html" % (BASE, it["slug"])) for _, it in scored[:max(1, limit)]]


def law(slug: str) -> Dict[str, Any]:
    with _lock:
        if slug in _laws:
            _laws.move_to_end(slug)
            return _laws[slug]
    act = _parse_zip(_fetch("%s/%s/xml.zip" % (BASE, slug)), slug)
    with _lock:
        _laws[slug] = act
        _laws.move_to_end(slug)
        while len(_laws) > _LAW_CACHE:
            _laws.popitem(last=False)
    return act


def _key(abbr: str) -> str:
    return re.sub(r"[\s.]", "", abbr).casefold()


def _resolve(kanun: str) -> Tuple[str, str]:
    """(slug, how) for an abbreviation, a page name or a full title."""
    raw = re.sub(r"\s+", " ", kanun.strip())
    if raw.upper() in SLUGS:
        return SLUGS[raw.upper()], "alias"
    if re.fullmatch(r"[a-z0-9_]+", raw):
        return raw, "slug"
    if " " in raw:
        return _from_title(raw), "title"
    return re.sub(r"[^a-z0-9]", "", raw.casefold()), "guess"


def _from_title(name: str) -> str:
    hits = search_laws(name, 5)
    exact = [h for h in hits if h["title"].casefold() == name.casefold()]
    if exact:
        return exact[0]["slug"]
    if len(hits) == 1:
        return hits[0]["slug"]
    raise GiiError("no single act matches %r on gesetze-im-internet.de" % name,
                   candidates=[{"title": h["title"], "slug": h["slug"]} for h in hits])


def _norm_key(label: str) -> str:
    s = label.strip().casefold()
    s = re.sub(r"^(§+|art\.?|artikel)\s*", "", s)
    return re.sub(r"\s+", "", s)


def norm_url(slug: str, enbez: str) -> str:
    key = _norm_key(enbez)
    if enbez.strip().startswith("§"):
        return "%s/%s/__%s.html" % (BASE, slug, key)
    if re.match(r"(?i)art", enbez.strip()):
        return "%s/%s/art_%s.html" % (BASE, slug, key)
    return "%s/%s/index.html" % (BASE, slug)


def norm(kanun: str, nr: str, page: int = 1) -> Dict[str, Any]:
    """One norm of one act, with its heading, the act's Stand line and its own page."""
    slug, how = _resolve(kanun)
    try:
        act = law(slug)
    except GiiError as exc:
        if exc.status != 404 or how != "guess":
            raise
        raise GiiError("no act with the page name %r; search the title with gesetz_ara" % slug,
                       status=404) from exc
    abbr = act["jurabk"] or act["amtabk"]
    if how == "guess" and _key(kanun) not in (_key(act["jurabk"]), _key(act["amtabk"])):
        raise GiiError("%r resolved to the page %r, which is %s (%s), not the act asked for"
                       % (kanun, slug, abbr, act["title"]))
    key = _norm_key(nr)
    hits = [n for n in act["norms"] if _norm_key(n["enbez"]) == key]
    if not hits:
        near = [n["enbez"] for n in act["norms"] if _norm_key(n["enbez"]).startswith(key[:1])][:12]
        raise GiiError("%s has no norm %r" % (abbr, nr), candidates=near)
    n = hits[0]
    pages = max(1, -(-len(n["text"]) // _PAGE_CHARS))
    page = min(max(1, page), pages)
    return {
        "law": abbr,
        "law_title": act["title"],
        "norm": n["enbez"],
        "heading": n["titel"],
        "text": n["text"][(page - 1) * _PAGE_CHARS: page * _PAGE_CHARS],
        "page": page,
        "pages": pages,
        "citation": "%s %s" % (n["enbez"], abbr),
        "stand": act["stand"],
        "source_url": norm_url(slug, n["enbez"]),
        "law_url": "%s/%s/index.html" % (BASE, slug),
        "source": "gesetze-im-internet.de (BMJ, juris)",
        "authenticity": AUTHENTICITY,
    }


def status() -> Dict[str, Any]:
    with _lock:
        out = {
            "server": "de-gii-mcp",
            "version": __version__,
            "upstream": BASE,
            "auth": "none",
            "toc_acts_cached": len(_toc["items"]),
            "toc_age_s": int(time.time() - _toc["at"]) if _toc["at"] else None,
            "acts_cached": list(_laws),
        }
    try:
        req = urllib.request.Request(TOC_URL, headers={"User-Agent": _UA, "Range": "bytes=0-200"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            out["upstream_reachable"] = resp.status in (200, 206)
    except Exception as exc:  # noqa: BLE001 - unreachable is a reportable state
        out["upstream_reachable"] = False
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out
