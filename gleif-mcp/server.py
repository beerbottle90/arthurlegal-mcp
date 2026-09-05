#!/usr/bin/env python3
"""gleif-mcp — the global LEI register over MCP. No auth, stdlib only.

    python server.py                                 # stdio
    python server.py --transport http --port 8050    # http://127.0.0.1:8050/mcp

No crawl step and no local index: GLEIF is an identity resolver, not a corpus.
What this server adds is the distinction the raw API leaves to the caller --
between the status of a company and the status of its LEI record, and between
"no parent" and "no answer".
"""
from __future__ import annotations

from typing import Any, Dict

from gleif import GleifClient, GleifError
from mcpcore import McpError, Tool, run
from retrieval import embeddings_status, semantic_rerank

__version__ = "1.0.0"

_client = GleifClient()

INSTRUCTIONS = """The Global LEI Index (GLEIF) — who a legal person is, and who
owns it. Used for counterparty identification, group mapping and KYC.

WHAT THIS IS NOT. GLEIF is not a corporate registry. It holds no filings, no
share capital, no directors, no financials. It holds identity, address, and
self-declared ownership that a validation agent has checked. For constitutive
documents go to the national registry named in `registered_at`.

TWO STATUSES, ROUTINELY CONFUSED. `entity_status` describes the company
(ACTIVE / INACTIVE). `registration_status` describes the LEI record
(ISSUED / LAPSED / RETIRED). An ACTIVE company can hold a LAPSED LEI: the
company exists, its reference data has simply not been revalidated. LAPSED
means stale, not dissolved -- do not report a lapsed record as evidence that a
counterparty has ceased to exist. Read `last_update` before relying on any
address or ownership field.

"NO PARENT" IS NOT ONE FINDING. GLEIF records three distinct reasons an entity
reports no parent: none exists, the parent holds no LEI, or disclosure is
legally obstructed. The API answers the same way in all three cases. When a
parent is absent, `*_parent_note` says so -- report the ambiguity, do not write
"no parent company".

OWNERSHIP IS ACCOUNTING CONSOLIDATION, NOT CONTROL. GLEIF parents are defined by
accounting consolidation. An entity controlled through contract, golden share or
a nominee will not appear as a child here.

NAMES. Resolve a rough name with `autocomplete` first -- it returns candidate
LEIs -- then fetch the record. Legal names are exact strings; "Siemens" and
"Siemens Aktiengesellschaft" are different filters.

CITATIONS. Copy `lei`, `legal_name` and `source_url` verbatim. Never construct
an LEI: it is 20 characters with a check digit and a wrong one resolves to a
different company or to nothing."""

TOOLS = [
    Tool(
        name="search_entities",
        description=(
            "Search the LEI register by legal name, jurisdiction, city or status. "
            "Legal name is an exact filter, not a substring search -- use "
            "`autocomplete` first when you only have a rough name. Results are "
            "reranked locally by relevance to your query."),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "exact legal name"},
                "jurisdiction": {"type": "string", "description": "ISO 3166 code, e.g. DE, TR, GB"},
                "city": {"type": "string"},
                "status": {"type": "string", "enum": ["ACTIVE", "INACTIVE"]},
                "page_size": {"type": "integer", "default": 10, "maximum": 200},
                "page": {"type": "integer", "default": 1},
            },
        },
        handler=lambda a: _t_search(a),
    ),
    Tool(
        name="autocomplete",
        description=(
            "Resolve a partial or approximate company name to candidate LEIs. "
            "Start here when you have a name from a contract rather than from a "
            "register."),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        handler=lambda a: _t_autocomplete(a),
    ),
    Tool(
        name="get_entity",
        description=(
            "Full LEI record: legal name, addresses, legal form, jurisdiction, "
            "both statuses and the date the record was last revalidated."),
        input_schema={
            "type": "object",
            "properties": {"lei": {"type": "string", "description": "20-character LEI"}},
            "required": ["lei"],
        },
        handler=lambda a: _t_get(a),
    ),
    Tool(
        name="get_group_structure",
        description=(
            "Direct and ultimate parent, and direct children, for one LEI. "
            "Ownership here means accounting consolidation -- control exercised "
            "by contract or nominee does not appear. An absent parent is "
            "reported with the reason it may be absent."),
        input_schema={
            "type": "object",
            "properties": {"lei": {"type": "string"}},
            "required": ["lei"],
        },
        handler=lambda a: _t_group(a),
    ),
    Tool(
        name="server_status",
        description="Upstream reachability and whether semantic reranking is live.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda a: _t_status(a),
    ),
]


def _t_search(args: Dict[str, Any]) -> Any:
    query = " ".join(str(args.get(k, "")) for k in ("name", "city", "jurisdiction")).strip()
    try:
        raw = _client.search(
            name=args.get("name", ""),
            jurisdiction=args.get("jurisdiction", ""),
            city=args.get("city", ""),
            status=args.get("status", ""),
            page_size=int(args.get("page_size", 10)),
            page=int(args.get("page", 1)),
        )
    except GleifError as exc:
        raise McpError(str(exc)) from exc
    ranked = semantic_rerank(query, raw["results"],
                             fields=("legal_name", "other_names", "legal_address"))
    return {
        "total_upstream": raw["total"],
        "page": raw["page"],
        "last_page": raw["last_page"],
        "returned": len(ranked["results"]),
        "golden_copy_date": raw["golden_copy_date"],
        "ranking": {"method": ranked["method"],
                    "note": ranked.get("note") or ranked.get("warning")},
        "scope_note": ("Identity and ownership only. GLEIF holds no filings, "
                       "financials or directors."),
        "results": ranked["results"],
    }


def _t_autocomplete(args: Dict[str, Any]) -> Any:
    try:
        hits = _client.autocomplete(args["query"], int(args.get("limit", 10)))
    except (GleifError, KeyError) as exc:
        raise McpError(str(exc)) from exc
    return {
        "query": args.get("query", ""),
        "returned": len(hits),
        "note": "Candidate names. Fetch the record with get_entity before citing anything.",
        "results": hits,
    }


def _t_get(args: Dict[str, Any]) -> Any:
    try:
        rec = _client.get(args["lei"])
    except (GleifError, KeyError) as exc:
        raise McpError(str(exc)) from exc
    rec["status_note"] = (
        "entity_status describes the company; registration_status describes the "
        "LEI record. LAPSED means the reference data is stale, not that the "
        "company is dissolved.")
    return rec


def _t_group(args: Dict[str, Any]) -> Any:
    try:
        out = _client.relationships(args["lei"])
    except (GleifError, KeyError) as exc:
        raise McpError(str(exc)) from exc
    out["scope_note"] = (
        "Parent/child here means accounting consolidation. Control through "
        "contract, golden share or nominee does not appear in GLEIF.")
    return out


def _t_status(args: Dict[str, Any]) -> Any:
    out: Dict[str, Any] = {
        "server": "gleif-mcp",
        "version": __version__,
        "upstream": "https://api.gleif.org/api/v1",
        "auth": "none",
        "index": "none — GLEIF is queried live; there is no local corpus to go stale",
    }
    try:
        probe = _client.search(jurisdiction="DE", page_size=1)
        out["upstream_reachable"] = True
        out["golden_copy_date"] = probe["golden_copy_date"]
    except GleifError as exc:
        out["upstream_reachable"] = False
        out["error"] = str(exc)
    out.update(embeddings_status())
    return out


if __name__ == "__main__":
    run(TOOLS, name="gleif-mcp", version=__version__, instructions=INSTRUCTIONS)
