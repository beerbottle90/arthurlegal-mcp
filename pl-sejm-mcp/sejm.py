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
Title search does not rank either: matches come back newest first, so a code
with a hundred amending acts is found last. See :func:`rank_title_matches`.

Responses are gzip-encoded, and ``urllib`` does not decompress automatically —
handled in ``_get``.
"""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
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

# One upstream page is the candidate pool for title ranking. 500 is the search
# endpoint's own default page size and covers most title queries whole
# ("Kodeks cywilny" 61 matches, "Kodeks karny" 153, "podatku dochodowym" 385).
TITLE_POOL = 500

# Entries per relation that get_act returns by default. The Civil Code's graph
# is 373 entries, 223 of them implementing acts.
REFERENCES_LIMIT = 10


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


# --------------------------------------------------------------------------- #
# Title ranking                                                                #
# --------------------------------------------------------------------------- #
# Polish act titles are "<type> z dnia <d> <month> <yyyy> r. <name>": the name
# follows the date, with or without a dash ("- Kodeks karny." / "Kodeks pracy.").
_DATED = re.compile(r"\bz dnia \d{1,2} \w+ \d{4} r\b")
# Names that announce a change to another act, a consolidated-text notice or a
# correction. They carry the searched phrase because they name their target.
_AMENDING = re.compile(
    r"^(o zmianie|zmieniajac\w*|o uchyleniu|uchylajac\w*|o sprostowaniu|sprostowanie"
    r"|w sprawie sprostowania|w sprawie ogloszenia jednolitego tekstu)\b")
# The same clauses mid-title: "... o ochronie nabywców ... oraz o zmianie ustawy
# - Kodeks cywilny" is its own act, but it names the Code only as a target.
_AMENDING_CLAUSE = re.compile(
    r"\b(zmianie|zmieniajac\w*|uchyleniu|uchylajac\w*|sprostowaniu|jednolitego tekstu)\b")

TITLE_MATCH_LABELS = (
    "exact title",              # 0: the act is named by the query
    "title starts with query",  # 1: e.g. "Kodeks karny skarbowy" for "Kodeks karny"
    "title contains query",     # 2: e.g. "Przepisy wprowadzające Kodeks karny"
    "amending act or notice",   # 3: names the act it amends, consolidates or corrects
    "words match elsewhere",    # 4: the upstream matched the words, not the phrase
)


def _fold(text: str) -> str:
    """Case-, diacritic- and punctuation-insensitive form for comparing titles."""
    text = (text or "").replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _without_o(text: str) -> str:
    # "o podatku dochodowym ..." is named by "podatku dochodowym ...".
    return text[2:] if text.startswith("o ") else text


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and (" %s " % needle) in (" %s " % haystack)


def title_match_tier(query: str, title: str) -> int:
    """How directly ``title`` names the act ``query`` asks for; 0 is best.

    Structural, not statistical: every candidate already matched the words
    upstream, so word statistics cannot tell the Code from its amendments —
    BM25 scored all of them alike. Position of the phrase in the title can.
    """
    q = _without_o(_fold(query))
    folded = _fold(title)
    if not q:
        return 4
    m = _DATED.search(folded)
    head = folded[m.end():].strip() if m else folded
    prefix = folded[:m.start()].strip() if m else ""
    name = _without_o(head)
    if _AMENDING.match(head) or "jednolitego tekstu" in folded:
        return 3 if (_contains(head, q) or _contains(prefix, q)) else 4
    if name == q or folded == q:
        return 0
    if name.startswith(q + " "):
        return 1
    if _contains(head, q):
        at = (" %s " % head).find(" %s " % q)
        return 3 if _AMENDING_CLAUSE.search(head[:at]) else 2
    if _contains(prefix, q):
        return 2
    return 4


def rank_title_matches(query: str, acts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order title-search candidates so the act bearing the name comes first.

    ``acts`` arrive in a base order (semantic or BM25 rerank of the upstream
    set) and keep it within a tier. Three groups, in this order:

    1. acts the title names (tiers 0-2): in force before repealed, then by
       tier — so the 1997 Kodeks karny leads, and for "podatku dochodowym" the
       two income-tax acts in force come before the repealed decrees of
       1946-1972 whose titles match exactly;
    2. amending acts and notices (tier 3), newest first, as amendments are read;
    3. word matches elsewhere (tier 4), in base order.

    Each act gains ``title_match``.
    """
    force_rank = {True: 0, None: 1, False: 2}
    keyed = []
    for i, act in enumerate(acts):
        tier = title_match_tier(query, act.get("title", ""))
        item = dict(act)
        item["title_match"] = TITLE_MATCH_LABELS[tier]
        group = 0 if tier <= 2 else tier - 2
        key = (
            group,
            force_rank.get(item.get("in_force"), 1) if group == 0 else 0,
            tier,
            item.get("_upstream_pos", i) if tier == 3 else 0,
        )
        keyed.append((key, i, item))
    keyed.sort(key=lambda k: (k[0], k[1]))
    return [item for _, _, item in keyed]


def _as_list(vals: Any) -> List[Dict[str, Any]]:
    return [v for v in (vals if isinstance(vals, list) else [vals]) if isinstance(v, dict)]


def _reference(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One graph entry, from either shape the API uses.

    /references gives ``{"act": {ELI, displayAddress, title, status, ...},
    "art", "date"}``; the act payload gives ``{"id", "art", "date"}``. No
    display address is ever built from an id: older ones carry a volume
    ("Dz.U. 1964 nr 16 poz. 94") that the id does not.
    """
    act = entry.get("act") if isinstance(entry.get("act"), dict) else None
    out: Dict[str, Any] = {"id": (act or {}).get("ELI") or entry.get("id", "")}
    if act:
        out["display_address"] = act.get("displayAddress", "")
        out["title"] = act.get("title", "")
        out["status"] = act.get("status", "")
    for key in ("art", "date"):
        if entry.get(key):
            out[key] = entry[key]
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
            # totalCount is every match; the API's own `count` is this page only.
            "count": data.get("totalCount", data.get("count", len(items))),
            "returned": len(items),
            "scope": "TITLE MATCH ONLY — the Sejm search endpoint does not read "
                     "act bodies. Use search_indexed for body/keyword search.",
            "results": items,
        }

    def search_pool(self, title: str, publisher: str = "", year: Optional[int] = None,
                    act_type: str = "", in_force_only: bool = False,
                    pool: int = TITLE_POOL) -> Dict[str, Any]:
        """One wide page of title matches, in upstream order, for ranking.

        Each act keeps its upstream position in ``_upstream_pos`` so a ranker
        can restore newest-first order where that is the meaningful order.
        """
        params: Dict[str, Any] = {
            "title": title, "publisher": publisher, "year": year,
            "type": act_type, "limit": max(1, min(int(pool), TITLE_POOL)), "offset": 0,
        }
        if in_force_only:
            params["inForce"] = 1
        data = _get("acts/search", params)
        items = []
        for i, raw in enumerate(data.get("items") or []):
            act = _norm(raw)
            act["_upstream_pos"] = i
            items.append(act)
        return {"count": data.get("totalCount", data.get("count", len(items))),
                "results": items}

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

    def _act_meta(self, publisher: str, year: int, pos: int) -> Dict[str, Any]:
        """The act's own record, without the amendment graph."""
        if publisher not in PUBLISHERS:
            raise SejmError("publisher must be DU or MP")
        return _norm(_get("acts/%s/%d/%d" % (publisher, int(year), int(pos))))

    def get_act(self, publisher: str, year: int, pos: int,
                references_limit: int = REFERENCES_LIMIT, relation: str = "",
                references_offset: int = 0) -> Dict[str, Any]:
        if publisher not in PUBLISHERS:
            raise SejmError("publisher must be DU or MP")
        path = "acts/%s/%d/%d" % (publisher, int(year), int(pos))
        data = _get(path)
        out = _norm(data)
        # references maps relationship -> list of related acts: which act amended
        # this one, which it repealed, and so on. The act payload holds only an
        # ELI id per entry; /references holds each related act's header, which is
        # where its citation (displayAddress) and title live.
        warning = ""
        graph: Dict[str, List[Dict[str, Any]]] = {}
        try:
            detailed = _get(path + "/references") or {}
            for rel, vals in detailed.items():
                graph[rel] = [_reference(v) for v in _as_list(vals)]
        except SejmError as exc:
            warning = ("Display addresses and titles are unavailable: the Sejm "
                       "references endpoint failed (%s). Entries carry only the "
                       "related act's ELI `id`." % exc)
            for rel, vals in (data.get("references") or {}).items():
                graph[rel] = [_reference(v) for v in _as_list(vals)]
        if graph:
            if relation and relation not in graph:
                raise SejmError("No relation %r for this act. Available: %s"
                                % (relation, ", ".join(sorted(graph))))
            limit = max(0, int(references_limit))
            start = max(0, int(references_offset)) if relation else 0
            names = [relation] if relation else list(graph)
            out["references"] = {rel: graph[rel][start: start + limit] for rel in names}
            out["references_total"] = {rel: len(graph[rel]) for rel in graph}
            if any(len(graph[rel]) > len(out["references"][rel]) for rel in names):
                out["references_truncated"] = (
                    "Showing %d per relation (references_limit) of the totals in "
                    "`references_total`. Page one relation with relation=<name> "
                    "and references_offset." % limit)
            out["references_note"] = (
                "`references` is the amendment graph. Check it before treating "
                "this text as current: an act can be in force and still have "
                "been amended many times. `date` on an amending act is when its "
                "change takes effect, and may lie in the future."
            )
            if warning:
                out["references_warning"] = warning
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
        meta = self._act_meta(publisher, year, pos)
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
