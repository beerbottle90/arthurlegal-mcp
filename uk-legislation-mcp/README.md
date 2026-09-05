# uk-legislation-mcp

**UK legislation** over MCP, from legislation.gov.uk (The National Archives,
Open Government Licence v3.0). No auth, Python standard library only.

The National Archives publish their own MCP server (`legislation-mcp-ts`), but
its product policy binds it to loopback — it is not meant to be hosted for
others. This server speaks to the same public data over the site's open XML and
Atom endpoints instead, so nothing is redistributed.

## Tools

| Tool | Purpose |
|---|---|
| `search_legislation` | By title, full text, type and year |
| `get_legislation` | Status, the date the text is current to, extent |
| `get_section` | The text of one section |
| `get_contents` | Table of contents |
| `get_effects` | Amendments by later legislation — **including unapplied ones** |
| `server_status` | Upstream reachability, semantic reranking state |

## What it adds over the raw API

**The amendment trap.** The revised text is current only to `text_current_to`.
Amendments enacted after that date, or awaiting editorial processing, are not in
the text you read. `get_effects` with `unapplied_only` walks the *whole* feed
rather than one page: the Equality Act 2010 carries 24 unapplied amendments that
a single page reports as none. `complete_scan` says whether the walk finished, so
a partial count reads as a floor rather than a total.

**Silently ignored filters.** legislation.gov.uk accepts a `year=` query
parameter and ignores it — same total, same first result. Year filtering belongs
in the path, and that is where this server puts it, so a filtered search is
really filtered. There is no range filter upstream; ask one year at a time.

**HTML served as data.** Several `.../data.xml` paths return the web page
instead. That is raised as an error rather than parsed into an empty result.

**No case law. Hard gate.** legislation.gov.uk holds statutes, not judgments.
English law is common law: the meaning of a section is often settled by authority
this server cannot see.

## Run

```
python server.py                                  # stdio
python server.py --transport http --port 8070     # http://127.0.0.1:8070/mcp
```

MIT licensed. Part of [arthurlegal-mcp](https://github.com/beerbottle90/arthurlegal-mcp).
