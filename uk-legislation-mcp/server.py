#!/usr/bin/env python3
"""uk-legislation-mcp — the UK statute book over MCP. No auth, stdlib only.

    python server.py                                 # stdio
    python server.py --transport http --port 8070    # http://127.0.0.1:8070/mcp

No crawl step: legislation.gov.uk searches its own corpus. What this server adds
is the amendment picture -- whether the text you are about to read has changes
made to it that the site has not yet applied.
"""
from __future__ import annotations

from typing import Any, Dict

from mcpcore import McpError, Tool, run
from retrieval import embeddings_status, semantic_rerank
from uklaw import TYPES, UkClient, UkError

__version__ = "1.0.0"

_client = UkClient()

INSTRUCTIONS = """UK legislation from legislation.gov.uk, published by The
National Archives under the Open Government Licence. Acts and statutory
instruments for the UK, Scotland, Wales and Northern Ireland, plus retained EU
law.

NO CASE LAW. HARD GATE. legislation.gov.uk holds statutes, not judgments. English
law is common law: the meaning of a section is often settled by authority this
server cannot see. Never present statutory text as the state of English law on a
contested point without saying that case law was not checked. For UK judgments
use BAILII or the National Archives' Find Case Law — neither is here.

THE AMENDMENT TRAP. The revised text is current only to `text_current_to`.
Amendments enacted after that date, or awaiting editorial processing, are NOT in
the text you read. `get_effects` with `unapplied_only` answers this, and it walks
the whole feed rather than one page — the Equality Act 2010 carries 24 unapplied
amendments that a single page would have reported as none. Always check before
advising on a live provision.

STATUS. `revised` means amendments have been incorporated up to a point.
`final` means as enacted, with no amendments incorporated at all. A `final`
document plus a long effects list is a provision you should not quote as current.

EXTENT ≠ APPLICATION. `extent` (E+W+S+N.I.) says where a provision forms part of
the law, not where it applies in fact. A section extending to Scotland may still
have no application to a Scottish transaction.

YEAR FILTERING. One year at a time. legislation.gov.uk filters by year in the
path; a year range is not supported upstream, and a year passed as a query
parameter is accepted and silently ignored — this server puts it in the path
instead so a filtered search is really filtered.

CITATIONS. Copy `title`, `year`, `number` and `source_url` verbatim. The short
title ("Arbitration Act 1996") is the citation; do not invent chapter numbers."""

TOOLS = [
    Tool(
        name="search_legislation",
        description=(
            "Search UK legislation by title, full text, type and year. Results "
            "are reranked locally by relevance. Year is one year at a time — "
            "there is no range filter upstream."),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "words in the short title"},
                "text": {"type": "string", "description": "full-text search"},
                "type": {"type": "string", "enum": sorted(TYPES), "default": "all"},
                "year": {"type": "string", "description": "single year, e.g. 1996"},
                "page": {"type": "integer", "default": 1},
            },
        },
        handler=lambda a: _t_search(a),
    ),
    Tool(
        name="get_legislation",
        description=(
            "Metadata for one item: status (revised / as enacted), the date the "
            "text is current to, extent and provision count."),
        input_schema={
            "type": "object",
            "properties": {
                "type": {"type": "string", "default": "ukpga"},
                "year": {"type": "string"},
                "number": {"type": "string"},
            },
            "required": ["year", "number"],
        },
        handler=lambda a: _t_get(a),
    ),
    Tool(
        name="get_section",
        description=(
            "The text of one section. Prefer this over fetching a whole Act. "
            "Check get_effects for that section before quoting it as current."),
        input_schema={
            "type": "object",
            "properties": {
                "type": {"type": "string", "default": "ukpga"},
                "year": {"type": "string"},
                "number": {"type": "string"},
                "section": {"type": "string"},
            },
            "required": ["year", "number", "section"],
        },
        handler=lambda a: _t_section(a),
    ),
    Tool(
        name="get_contents",
        description="Table of contents for one item — section numbers and headings.",
        input_schema={
            "type": "object",
            "properties": {
                "type": {"type": "string", "default": "ukpga"},
                "year": {"type": "string"},
                "number": {"type": "string"},
            },
            "required": ["year", "number"],
        },
        handler=lambda a: _t_contents(a),
    ),
    Tool(
        name="get_effects",
        description=(
            "Amendments, repeals and insertions made to this item by later "
            "legislation. Set unapplied_only to find changes that are law but "
            "are NOT yet in the published text — the single most common way to "
            "quote a superseded provision. With a filter the whole feed is "
            "walked; `complete_scan` says whether it finished."),
        input_schema={
            "type": "object",
            "properties": {
                "type": {"type": "string", "default": "ukpga"},
                "year": {"type": "string"},
                "number": {"type": "string"},
                "unapplied_only": {"type": "boolean", "default": False},
                "page": {"type": "integer", "default": 1},
                "max_pages": {"type": "integer", "default": 20,
                              "description": "pages to walk when filtering (50 effects each)"},
            },
            "required": ["year", "number"],
        },
        handler=lambda a: _t_effects(a),
    ),
    Tool(
        name="server_status",
        description="Upstream reachability and whether semantic reranking is live.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda a: _t_status(a),
    ),
]


def _t_search(args: Dict[str, Any]) -> Any:
    query = " ".join(str(args.get(k, "")) for k in ("title", "text")).strip()
    try:
        raw = _client.search(
            title=args.get("title", ""),
            text=args.get("text", ""),
            doc_type=args.get("type", "all"),
            year=args.get("year", ""),
            page=int(args.get("page", 1)),
        )
    except UkError as exc:
        raise McpError(str(exc)) from exc
    ranked = semantic_rerank(query, raw["results"], fields=("title", "doc_type"))
    return {
        "total_upstream": raw["total"],
        "page": raw["page"],
        "type": raw["type_label"],
        "year": raw["year"],
        "returned": len(ranked["results"]),
        "ranking": {"method": ranked["method"],
                    "note": ranked.get("note") or ranked.get("warning")},
        "scope_note": "Legislation only — legislation.gov.uk holds no case law.",
        "results": ranked["results"],
    }


def _t_get(args: Dict[str, Any]) -> Any:
    try:
        return _client.get(args.get("type", "ukpga"), args["year"], args["number"])
    except (UkError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def _t_section(args: Dict[str, Any]) -> Any:
    try:
        out = _client.get_section(args.get("type", "ukpga"), args["year"],
                                  args["number"], args["section"])
    except (UkError, KeyError) as exc:
        raise McpError(str(exc)) from exc
    out["warning"] = (
        "This is the published text. Amendments not yet applied do not appear "
        "in it — call get_effects with unapplied_only before quoting.")
    return out


def _t_contents(args: Dict[str, Any]) -> Any:
    try:
        return _client.contents(args.get("type", "ukpga"), args["year"], args["number"])
    except (UkError, KeyError) as exc:
        raise McpError(str(exc)) from exc


def _t_effects(args: Dict[str, Any]) -> Any:
    applied = False if args.get("unapplied_only") else None
    try:
        out = _client.effects(args.get("type", "ukpga"), args["year"], args["number"],
                              applied=applied, page=int(args.get("page", 1)),
                              max_pages=int(args.get("max_pages", 20)))
    except (UkError, KeyError) as exc:
        raise McpError(str(exc)) from exc
    if applied is False and not out.get("complete_scan"):
        out["warning"] = (
            "The scan stopped before the end of the feed, so `unapplied_found` "
            "is a floor, not a total. Raise max_pages to finish it.")
    return out


def _t_status(args: Dict[str, Any]) -> Any:
    out: Dict[str, Any] = {
        "server": "uk-legislation-mcp",
        "version": __version__,
        "upstream": "https://www.legislation.gov.uk",
        "auth": "none",
        "licence": "Open Government Licence v3.0",
        "coverage": "UK, Scottish, Welsh and Northern Irish legislation, and retained EU law — no case law",
        "index": "none — legislation.gov.uk is queried live; there is no local corpus to go stale",
    }
    try:
        probe = _client.search(title="Arbitration", doc_type="ukpga", year="1996")
        out["upstream_reachable"] = True
        out["probe_hits"] = probe["total"]
    except UkError as exc:
        out["upstream_reachable"] = False
        out["error"] = str(exc)
    out.update(embeddings_status())
    return out


if __name__ == "__main__":
    run(TOOLS, name="uk-legislation-mcp", version=__version__, instructions=INSTRUCTIONS)
