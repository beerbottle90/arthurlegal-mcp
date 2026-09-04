#!/usr/bin/env python3
"""pl-sejm-mcp — Polish legislation (Dz.U. / M.P.) over MCP. No auth, stdlib only.

    python server.py                                 # stdio
    python server.py --transport http --port 8000    # http://127.0.0.1:8000/mcp

Optional, for subject search across acts:

    python crawl.py --from 2015 --to 2026
"""

from __future__ import annotations

from typing import Any, Dict

from mcpcore import McpError, Tool, run
from retrieval import Index, embeddings_status, rerank
from sejm import PUBLISHERS, SejmClient, SejmError

__version__ = "1.0.0"

_client = SejmClient()
_index = Index()

INSTRUCTIONS = """Polish legislation from the Sejm's ELI API — Dziennik Ustaw
(Journal of Laws) and Monitor Polski.

STATUS IS PART OF THE CITATION. Every act carries the publisher's own status:
`obowiązujący` = in force, `uchylony` = repealed. Always report it. Citing a
Polish act without its status is an incomplete citation, the same discipline the
Azerbaijani e-qanun source follows.

AMENDMENTS. `get_act` returns a `references` graph with the publisher's own
relation names — `Akty zmieniające` (amending acts), `Akty uchylone` (repealed
acts), `Akty wykonawcze` (implementing acts). An act can be `obowiązujący` and
still have been amended many times; check the graph before treating a text as
current.

TWO SEARCHES, DIFFERENT REACH.
- `search_by_title` hits the live API but matches TITLES ONLY.
- `search_indexed` hits the local index and covers titles plus the Sejm's own
  subject keywords — use it for "which act governs X".
Neither searches article text: most acts are PDF-only (`textHTML: false`), so
there is no HTML body to index. For article-level work, open the act's PDF.

CITATIONS. Copy `display_address` (e.g. `Dz.U. 2024 poz. 1984`) and `citation`
verbatim. Never construct a Dz.U. number."""


def _t_search_title(args: Dict[str, Any]) -> Any:
    try:
        result = _client.search(
            title=args.get("title", ""),
            publisher=args.get("publisher", ""),
            year=args.get("year"),
            act_type=args.get("act_type", ""),
            in_force_only=bool(args.get("in_force_only")),
            limit=int(args.get("limit", 20)),
            offset=int(args.get("offset", 0)),
        )
    except SejmError as exc:
        raise McpError(str(exc)) from exc
    if args.get("title"):
        result["results"] = rerank(args["title"], result["results"], fields=("title",))
    return result


def _t_search_indexed(args: Dict[str, Any]) -> Any:
    query = (args.get("query") or "").strip()
    if not query:
        raise McpError("query is required")
    if _index.count() == 0:
        raise McpError(
            "The local index is empty — run `python crawl.py --from 2015 --to 2026`. "
            "search_by_title works without it (titles only)."
        )
    filters: Dict[str, Any] = {}
    if args.get("in_force_only"):
        filters["status"] = "obowiązujący"
    for key in ("date_from", "date_to"):
        if args.get(key):
            filters[key] = args[key]
    out = _index.search(
        query,
        mode=args.get("mode", "hybrid"),
        limit=int(args.get("limit", 20)),
        filters=filters,
    )
    out["coverage"] = _index.get_state("coverage") or "unknown — check server_status"
    out["scope_note"] = (
        "Covers titles, act types, issuing bodies and the Sejm's subject "
        "keywords — NOT article text."
    )
    return out


def _t_get_act(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_act(args["publisher"], int(args["year"]), int(args["pos"]))
    except (SejmError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _t_get_text(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_text(
            args["publisher"], int(args["year"]), int(args["pos"]),
            fmt=args.get("fmt", "html"), max_chars=int(args.get("max_chars", 60000)),
        )
    except (SejmError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _t_list_year(args: Dict[str, Any]) -> Any:
    try:
        return _client.list_year(
            args.get("publisher", "DU"), int(args["year"]),
            limit=int(args.get("limit", 50)), offset=int(args.get("offset", 0)),
        )
    except (SejmError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _t_status(args: Dict[str, Any]) -> Any:
    return {
        "server": "pl-sejm-mcp",
        "version": __version__,
        "source": "api.sejm.gov.pl/eli — public, no auth, ELI-compliant",
        "publishers": PUBLISHERS,
        "indexed_documents": _index.count(),
        "index_coverage": _index.get_state("coverage") or "not crawled",
        "last_crawl": _index.get_state("last_crawl") or "never — run crawl.py",
        "index_scope": "titles + act type + issuing body + Sejm subject keywords "
                       "(not article text)",
        **embeddings_status(),
    }


_LOC = {
    "publisher": {"type": "string", "enum": sorted(PUBLISHERS), "default": "DU"},
    "year": {"type": "integer"},
    "pos": {"type": "integer", "description": "Position within the year (the 'poz.' number)."},
}

TOOLS = [
    Tool(
        "search_by_title",
        "Search Polish acts by TITLE via the live Sejm API. Precise when you know "
        "the act's name ('Prawo energetyczne', 'Kodeks spółek handlowych'). It "
        "does NOT read act bodies — for subject search use search_indexed. Every "
        "result carries the publisher's `status`; report it.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Polish title words, e.g. 'energetyczne'."},
                "publisher": {"type": "string", "enum": sorted(PUBLISHERS)},
                "year": {"type": "integer"},
                "act_type": {"type": "string", "description": "e.g. 'Ustawa', 'Rozporządzenie'."},
                "in_force_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
            },
        },
        _t_search_title,
    ),
    Tool(
        "search_indexed",
        "Subject search over the local index: titles, act types, issuing bodies "
        "and the Sejm's own controlled keywords. Hybrid retrieval (BM25 + fuzzy, "
        "plus dense vectors when EMBEDDINGS_URL is configured). Use this for "
        "'which act governs X'. Check `coverage` — the index holds the year range "
        "that was crawled, not all of Polish law.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Polish terms work best."},
                "mode": {
                    "type": "string",
                    "enum": ["hybrid", "lexical", "semantic", "fuzzy"],
                    "default": "hybrid",
                },
                "in_force_only": {"type": "boolean", "default": False},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        _t_search_indexed,
    ),
    Tool(
        "get_act",
        "Full metadata for one act: title, status, entry into force, ELI, subject "
        "keywords, transposed EU directives, and the `references` amendment graph "
        "(which acts amended, repealed or implement it). Read the graph before "
        "treating the text as current law.",
        {"type": "object", "properties": dict(_LOC), "required": ["publisher", "year", "pos"]},
        _t_get_act,
    ),
    Tool(
        "get_act_text",
        "Text of an act. fmt='html' returns the text; fmt='pdf' returns the PDF "
        "URL (binary is not inlined). Many Polish acts are PDF-only — this checks "
        "the publisher's textHTML flag first and tells you plainly rather than "
        "returning an empty body.",
        {
            "type": "object",
            "properties": {
                **_LOC,
                "fmt": {"type": "string", "enum": ["html", "pdf"], "default": "html"},
                "max_chars": {"type": "integer", "default": 60000},
            },
            "required": ["publisher", "year", "pos"],
        },
        _t_get_text,
    ),
    Tool(
        "list_year",
        "Everything published by Dz.U. or M.P. in a given year. Useful for "
        "regulatory monitoring and for finding an act when you know roughly when "
        "it appeared.",
        {
            "type": "object",
            "properties": {
                "publisher": {"type": "string", "enum": sorted(PUBLISHERS), "default": "DU"},
                "year": {"type": "integer"},
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["year"],
        },
        _t_list_year,
    ),
    Tool(
        "server_status",
        "Index size and coverage, last crawl, and whether semantic search is on. "
        "Call this to tell an un-crawled index apart from a genuinely empty result.",
        {"type": "object", "properties": {}},
        _t_status,
    ),
]


if __name__ == "__main__":
    run(TOOLS, name="pl-sejm-mcp", version=__version__, instructions=INSTRUCTIONS)
