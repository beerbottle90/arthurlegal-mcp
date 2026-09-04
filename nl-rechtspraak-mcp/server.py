#!/usr/bin/env python3
"""nl-rechtspraak-mcp — Dutch case law and legislation over MCP. No auth, stdlib only.

    python server.py                                 # stdio
    python server.py --transport http --port 8000    # http://127.0.0.1:8000/mcp

Legislation search works immediately (KOOP SRU has real full-text search).
Case-law search needs an index first:

    python crawl.py --from 2024-01-01 --to 2026-08-30
"""

from __future__ import annotations

from typing import Any, Dict

from mcpcore import McpError, Tool, run
from rechtspraak import KoopClient, NlError, RechtspraakClient
from retrieval import Index, embeddings_status

__version__ = "1.0.0"

_cases = RechtspraakClient()
_laws = KoopClient()
_index = Index()

INSTRUCTIONS = """Dutch law: case law from Rechtspraak Open Data and legislation
from the KOOP SRU repository.

THE ONE THING TO INTERNALISE. The official Dutch case-law API has **no free-text
search**, and it silently ignores parameters it does not recognise — passing
`q=energie` returns all 3,751,381 decisions while looking like a search result.
This server therefore never forwards a keyword to that API. Case-law keyword
search is served from a LOCAL INDEX (`search_caselaw`), whose coverage is
whatever has been crawled. Call `server_status` to see that coverage before
concluding something does not exist. `browse_caselaw` uses only the filters the
API genuinely honours (date, court, legal area).

Legislation is the opposite: `search_legislation` passes a real CQL full-text
query to KOOP and searches the whole corpus.

ECLI DISCIPLINE. An ECLI is the citation. Copy it verbatim from a result; never
construct or guess one. A malformed ECLI is rejected rather than guessed at.

TEXT AVAILABILITY. Rechtspraak publishes metadata for far more decisions than it
publishes texts. A decision with an empty body is normal and is flagged; it does
not mean the case does not exist."""


def _t_search_caselaw(args: Dict[str, Any]) -> Any:
    query = (args.get("query") or "").strip()
    if not query:
        raise McpError("query is required")
    if _index.count() == 0:
        raise McpError(
            "The case-law index is empty. Dutch case law cannot be keyword-searched "
            "upstream, so this server needs a crawl first: "
            "`python crawl.py --from 2024-01-01 --to 2026-08-30`. "
            "browse_caselaw and get_decision work without an index."
        )
    filters: Dict[str, Any] = {}
    for key, col in (("court", "court"), ("subject", "subject"),
                     ("date_from", "date_from"), ("date_to", "date_to")):
        if args.get(key):
            filters[col] = args[key]
    out = _index.search(
        query,
        mode=args.get("mode", "hybrid"),
        limit=int(args.get("limit", 20)),
        filters=filters,
    )
    out["index_coverage"] = _index.get_state("coverage") or "unknown — call server_status"
    out["coverage_warning"] = (
        "Searches the LOCAL index only, not all 3.75M Dutch decisions. Absence "
        "from these results is not evidence that no such decision exists."
    )
    return out


def _t_browse_caselaw(args: Dict[str, Any]) -> Any:
    if args.get("query") or args.get("keyword") or args.get("q"):
        raise McpError(
            "This tool cannot do keyword search — the upstream API has none and "
            "would silently ignore the parameter, returning all 3.75M decisions "
            "as if they matched. Use search_caselaw for keywords, or filter here "
            "by date / court / subject."
        )
    try:
        return _cases.search(
            max_results=int(args.get("limit", 50)),
            offset=int(args.get("offset", 0)),
            date=args.get("date", ""),
            date_to=args.get("date_to", ""),
            subject=args.get("subject", ""),
            creator=args.get("court", ""),
            doc_type=args.get("doc_type", ""),
            modified_since=args.get("modified_since", ""),
        )
    except NlError as exc:
        raise McpError(str(exc)) from exc


def _t_get_decision(args: Dict[str, Any]) -> Any:
    try:
        return _cases.get_decision(args["ecli"], max_chars=int(args.get("max_chars", 60000)))
    except (NlError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def _t_search_legislation(args: Dict[str, Any]) -> Any:
    try:
        out = _laws.search(args["query"], start=int(args.get("start", 1)),
                           limit=int(args.get("limit", 20)))
    except (NlError, KeyError) as exc:
        raise McpError(str(exc)) from exc
    out["scope"] = ("Full-text CQL search across the whole KOOP repository — "
                    "not limited by any local index.")
    return out


def _t_vocabulary(args: Dict[str, Any]) -> Any:
    try:
        return {"values": _cases.vocabulary(args.get("which", "Rechtsgebieden"))}
    except NlError as exc:
        raise McpError(str(exc)) from exc


def _t_status(args: Dict[str, Any]) -> Any:
    return {
        "server": "nl-rechtspraak-mcp",
        "version": __version__,
        "sources": {
            "case law": "data.rechtspraak.nl Open Data — 3,751,381 ECLIs, no auth",
            "legislation": "repository.overheid.nl SRU 2.0 — full-text CQL, no auth",
        },
        "indexed_decisions": _index.count(),
        "index_coverage": _index.get_state("coverage") or "not crawled",
        "last_crawl": _index.get_state("last_crawl") or "never — run crawl.py",
        "upstream_quirk": (
            "data.rechtspraak.nl has no free-text search and silently ignores "
            "unknown parameters: ?q=, ?keyword= and ?text= all return the "
            "unfiltered 3,751,381. Verified 2026-08-30."
        ),
        **embeddings_status(),
    }


TOOLS = [
    Tool(
        "search_caselaw",
        "Keyword and concept search over Dutch decisions in the LOCAL index. This "
        "is the only way to search Dutch case law by words — the official API "
        "cannot. Hybrid retrieval (BM25 + fuzzy, plus dense vectors when "
        "EMBEDDINGS_URL is set). ALWAYS read `index_coverage` in the response: "
        "the index holds what was crawled, not all 3.75M decisions, so a miss is "
        "not proof of absence.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dutch legal terms work best, e.g. 'overmacht levering', 'kartelverbod'."},
                "mode": {"type": "string", "enum": ["hybrid", "lexical", "semantic", "fuzzy"], "default": "hybrid"},
                "court": {"type": "string", "description": "Exact court name as indexed, e.g. 'Raad van State'."},
                "subject": {"type": "string", "description": "Legal area, e.g. 'Bestuursrecht'."},
                "date_from": {"type": "string", "description": "ISO date lower bound."},
                "date_to": {"type": "string", "description": "ISO date upper bound."},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        _t_search_caselaw,
    ),
    Tool(
        "browse_caselaw",
        "Browse decisions using the filters the official API genuinely honours: "
        "date (or a date range), court, legal area, document type. NO KEYWORD "
        "SEARCH — passing one is refused rather than silently ignored. Use it to "
        "enumerate a court's output over a period, or to find recent decisions.",
        {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD. With date_to, forms a range."},
                "date_to": {"type": "string", "description": "YYYY-MM-DD upper bound."},
                "court": {"type": "string", "description": "Instantie value from list_vocabulary('Instanties')."},
                "subject": {"type": "string", "description": "Rechtsgebied URI from list_vocabulary('Rechtsgebieden')."},
                "doc_type": {"type": "string", "enum": ["Uitspraak", "Conclusie"]},
                "modified_since": {"type": "string", "description": "Only records changed since this date."},
                "limit": {"type": "integer", "default": 50, "description": "Max 1000."},
                "offset": {"type": "integer", "default": 0},
            },
        },
        _t_browse_caselaw,
    ),
    Tool(
        "get_decision",
        "Full text and metadata of one decision by ECLI. Returns court, date, "
        "docket number, procedure, legal area and the judgment body. A "
        "`conclusie` is an Advocate-General's opinion, not a judgment — the "
        "`document_kind` field says which. Copy the ECLI from a search result; "
        "malformed ones are rejected, not guessed.",
        {
            "type": "object",
            "properties": {
                "ecli": {"type": "string", "description": "e.g. ECLI:NL:HR:2024:1"},
                "max_chars": {"type": "integer", "default": 60000},
            },
            "required": ["ecli"],
        },
        _t_get_decision,
    ),
    Tool(
        "search_legislation",
        "Full-text search of Dutch legislation and official publications via KOOP "
        "SRU 2.0. Unlike case law this IS a real upstream search across the whole "
        "repository — verified to discriminate ('energiewet' 1,961 records, "
        "'mededinging' 20,991). Returns titles, identifiers and wetten.overheid.nl "
        "links.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dutch terms, e.g. 'energiewet', 'warmtewet'."},
                "start": {"type": "integer", "default": 1, "description": "1-indexed record offset."},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        _t_search_legislation,
    ),
    Tool(
        "list_vocabulary",
        "Controlled values for the browse_caselaw filters: 'Rechtsgebieden' "
        "(4 legal areas), 'Instanties' (261 courts), 'Proceduresoorten'. Use "
        "these exact values — invented ones are silently ignored upstream.",
        {
            "type": "object",
            "properties": {
                "which": {
                    "type": "string",
                    "enum": ["Rechtsgebieden", "Instanties", "Proceduresoorten"],
                    "default": "Rechtsgebieden",
                }
            },
        },
        _t_vocabulary,
    ),
    Tool(
        "server_status",
        "Index size, which date ranges were crawled at which depth, last crawl "
        "time, and whether semantic search is active. Call this before concluding "
        "that a Dutch decision does not exist.",
        {"type": "object", "properties": {}},
        _t_status,
    ),
]


if __name__ == "__main__":
    run(TOOLS, name="nl-rechtspraak-mcp", version=__version__, instructions=INSTRUCTIONS)
