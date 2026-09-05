# eu-cellar-mcp

**EU law** over MCP, through CELLAR — the Publications Office's semantic
repository behind EUR-Lex. No auth, Python standard library only.

## Why not eur-lex.europa.eu

Its document paths (`legal-content/`, `search.html`, `eli/`) return a JavaScript
shell. Fetching one yields the Official Journal date index for every request —
content that parses cleanly and is not the document asked for. CELLAR is the
machine-readable route to the same corpus and the only one that works from a
server.

Full text takes three steps, none skippable:

1. `https://publications.europa.eu/resource/celex/{CELEX}` → 303 to a work UUID
2. SPARQL: which manifestations exist for that work in the wanted language
3. `{manifestation}/DOC_1` → the text

## Tools

| Tool | Purpose |
|---|---|
| `search_eu_law` | Title search across legislation and CJEU case law |
| `get_metadata` | Title, date, ELI, in-force flag for one CELEX |
| `get_document_text` | Authentic full text, paginated (GDPR ≈ 350,000 chars) |
| `sparql` | SELECT escape hatch for the CELLAR graph |
| `server_status` | Upstream reachability, semantic reranking state |

## What it adds

**Consolidated ≠ original.** A CELEX beginning `0` with a date suffix
(`02010D0146-20161217`) is a consolidated text — the act as amended to that date,
prepared for information and **without legal force**. Marked `consolidated: true`
and warned about on fetch.

**Language is not a rendering option.** Each language is a separate expression
that either exists or does not. A missing one is an error, not a silent fallback.

**Case law is in here too.** CELEX prefix `6` is a judgment, `3` legislation.

## Run

```
python server.py                                  # stdio
python server.py --transport http --port 8080     # http://127.0.0.1:8080/mcp
```

MIT licensed. Part of [arthurlegal-mcp](https://github.com/beerbottle90/arthurlegal-mcp).
