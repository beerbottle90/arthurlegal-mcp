# gleif-mcp

The **Global LEI Index** over MCP — who a legal person is, and who owns it.
No auth, Python standard library only.

Upstream: `https://api.gleif.org/api/v1` (GLEIF, no key).

## Tools

| Tool | Purpose |
|---|---|
| `search_entities` | Filter the register by legal name, jurisdiction, city, status |
| `autocomplete` | Resolve a rough name to candidate LEIs — start here |
| `get_entity` | Full record: identity, addresses, legal form, both statuses |
| `get_group_structure` | Direct and ultimate parent, direct children |
| `server_status` | Upstream reachability, semantic reranking state |

## What it adds over the raw API

**Two statuses, routinely confused.** `entity_status` describes the company
(ACTIVE / INACTIVE); `registration_status` describes the LEI record
(ISSUED / LAPSED / RETIRED). An ACTIVE company can hold a LAPSED LEI — the
company exists, its reference data has simply not been revalidated. Reporting a
lapsed record as evidence of dissolution is the failure this separation prevents.

**"No parent" is not one finding.** GLEIF records three distinct reasons an
entity reports no parent — none exists, the parent holds no LEI, or disclosure is
legally obstructed — and answers 404 in all three cases. The tool returns the
ambiguity rather than an absence.

**Ownership is accounting consolidation, not control.** An entity controlled
through contract, golden share or nominee does not appear as a child.

GLEIF is not a corporate registry: no filings, no directors, no financials.

## Run

```
python server.py                                  # stdio
python server.py --transport http --port 8050     # http://127.0.0.1:8050/mcp
```

Set `EMBEDDINGS_URL`, `EMBEDDINGS_MODEL` and `EMBEDDINGS_API_KEY` to enable
semantic reranking; without them results fall back to BM25 and say so.

MIT licensed. Part of [arthurlegal-mcp](https://github.com/beerbottle90/arthurlegal-mcp).
