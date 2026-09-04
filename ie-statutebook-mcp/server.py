#!/usr/bin/env python3
"""ie-statutebook-mcp — Irish Acts over MCP. No auth, standard library only.

    python server.py                                 # stdio
    python server.py --transport http --port 8000    # http://127.0.0.1:8000/mcp

Search needs an index; direct fetches do not:

    python crawl.py --from 2015 --to 2026
"""

from __future__ import annotations

from typing import Any, Dict

from mcpcore import McpError, Tool, run
from retrieval import Index, embeddings_status
from statutebook import IeError, StatuteBookClient

__version__ = "1.0.0"

_client = StatuteBookClient()
_index = Index()

INSTRUCTIONS = """Irish primary legislation from the Irish Statute Book
(irishstatutebook.ie), the official publication of the Oireachtas.

AS ENACTED vs REVISED. Everything defaults to the Act **as enacted** — the text
as passed, with no later amendments applied. The Revised (consolidated)
collection is separate and does not cover every Act. Every response says which
version it is. Never present an "as enacted" text as the current law without
saying so.

SECTION, NOT ACT. Irish Acts are long — the Companies Act 2014 has 1,448
sections and 106,000 characters. Use `get_section` when the question is about a
provision; `get_act` only when you genuinely need the whole instrument.

SEARCH IS LOCAL. The Statute Book publishes no search endpoint (`/search` 404s),
so `search_acts` runs against a local index whose coverage is whatever was
crawled. Read `index_coverage` before concluding an Act does not exist.

CITATIONS. Irish Acts cite as "Title (No. N of YEAR)" — copy the `citation`
field. This server covers Acts of the Oireachtas, not Statutory Instruments and
not case law (for Irish judgments use courts.ie or BAILII; CourtListener is US
law and will not help)."""


def _t_search(args: Dict[str, Any]) -> Any:
    query = (args.get("query") or "").strip()
    if not query:
        raise McpError("query is required")
    if _index.count() == 0:
        raise McpError(
            "The index is empty — run `python crawl.py --from 2015 --to 2026`. "
            "get_act and get_section work without an index if you know the "
            "year and number."
        )
    filters: Dict[str, Any] = {}
    if args.get("year_from"):
        filters["date_from"] = "%d-01-01" % int(args["year_from"])
    if args.get("year_to"):
        filters["date_to"] = "%d-12-31" % int(args["year_to"])
    out = _index.search(
        query,
        mode=args.get("mode", "hybrid"),
        limit=int(args.get("limit", 20)),
        filters=filters,
    )
    out["index_coverage"] = _index.get_state("coverage") or "unknown — call server_status"
    out["version_note"] = "Indexed texts are AS ENACTED — amendments not applied."
    return out


def _t_get_act(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_act(
            int(args["year"]), int(args["number"]),
            version=args.get("version", "enacted"),
            max_chars=int(args.get("max_chars", 60000)),
        )
    except (IeError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _t_get_section(args: Dict[str, Any]) -> Any:
    try:
        return _client.get_section(
            int(args["year"]), int(args["number"]), str(args["section"]),
            version=args.get("version", "enacted"),
            max_chars=int(args.get("max_chars", 30000)),
        )
    except (IeError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc


def _t_list_year(args: Dict[str, Any]) -> Any:
    try:
        acts = _client.list_year(int(args["year"]),
                                 probe_limit=int(args.get("probe_limit", 60)))
    except (IeError, KeyError, ValueError) as exc:
        raise McpError(str(exc)) from exc
    # Bodies would be enormous here and the caller asked for a list.
    return {
        "year": int(args["year"]),
        "count": len(acts),
        "method": "Act numbers probed sequentially — the Statute Book has no "
                  "year-index API.",
        "results": [{k: v for k, v in a.items() if k != "text"} for a in acts],
    }


def _t_status(args: Dict[str, Any]) -> Any:
    return {
        "server": "ie-statutebook-mcp",
        "version": __version__,
        "source": "irishstatutebook.ie — official, public, no auth",
        "coverage_type": "Acts of the Oireachtas, as enacted (not SIs, not case law)",
        "indexed_documents": _index.count(),
        "index_coverage": _index.get_state("coverage") or "not crawled",
        "last_crawl": _index.get_state("last_crawl") or "never — run crawl.py",
        "upstream_quirk": "No search endpoint: /search and /searchresults.html "
                          "both 404. Search is served locally.",
        **embeddings_status(),
    }


_LOC = {
    "year": {"type": "integer", "description": "Year of the Act, e.g. 2014."},
    "number": {"type": "integer", "description": "Act number within the year, e.g. 38."},
    "version": {"type": "string", "enum": ["enacted", "revised"], "default": "enacted"},
}

TOOLS = [
    Tool(
        "search_acts",
        "Search the full text of Irish Acts in the local index. Hybrid retrieval "
        "(BM25 + fuzzy, plus dense vectors when EMBEDDINGS_URL is set). Unlike "
        "Spain, whole Act bodies are indexed here, so a phrase inside a section "
        "IS findable. Check `index_coverage` — the index holds the crawled year "
        "range, not all of Irish law.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "e.g. 'financial assistance for acquisition of shares'."},
                "mode": {"type": "string", "enum": ["hybrid", "lexical", "semantic", "fuzzy"], "default": "hybrid"},
                "year_from": {"type": "integer"},
                "year_to": {"type": "integer"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        _t_search,
    ),
    Tool(
        "get_section",
        "One section of an Act — the right tool for almost every provision-level "
        "question. Section may be '1', '82' or '12A'. Returns the section text as "
        "enacted unless version='revised'.",
        {
            "type": "object",
            "properties": {
                **_LOC,
                "section": {"type": "string", "description": "Section number, e.g. '82' or '12A'."},
                "max_chars": {"type": "integer", "default": 30000},
            },
            "required": ["year", "number", "section"],
        },
        _t_get_section,
    ),
    Tool(
        "get_act",
        "The whole Act. Long instruments are truncated with an explicit marker — "
        "the Companies Act 2014 alone is ~106,000 characters — so prefer "
        "get_section unless you really need the entire text.",
        {
            "type": "object",
            "properties": {**_LOC, "max_chars": {"type": "integer", "default": 60000}},
            "required": ["year", "number"],
        },
        _t_get_act,
    ),
    Tool(
        "list_year",
        "List the Acts passed in a given year, with titles and citations. Works "
        "by probing Act numbers because the Statute Book publishes no year index; "
        "raise probe_limit for a year with unusually many Acts.",
        {
            "type": "object",
            "properties": {
                "year": {"type": "integer"},
                "probe_limit": {"type": "integer", "default": 60},
            },
            "required": ["year"],
        },
        _t_list_year,
    ),
    Tool(
        "server_status",
        "Index size and coverage, last crawl, and whether semantic search is on.",
        {"type": "object", "properties": {}},
        _t_status,
    ),
]


if __name__ == "__main__":
    run(TOOLS, name="ie-statutebook-mcp", version=__version__, instructions=INSTRUCTIONS)
