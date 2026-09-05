# jp-egov-mcp

**Japanese statute law** over MCP, from e-Gov 法令API v2 (Digital Agency).
No auth, Python standard library only.

Upstream: `https://laws.e-gov.go.jp/api/2`

## Tools

| Tool | Purpose |
|---|---|
| `search_laws` | Full-text keyword search, grouped by statute |
| `list_laws` | Browse the statute list with metadata |
| `get_law` | Metadata and in-force status by `law_id` |
| `get_law_text` | Consolidated full text, paginated by characters |
| `get_article` | One article — `"331"` for 第三百三十一条 |
| `server_status` | Upstream reachability, semantic reranking state |

## What it adds over the raw API

**One field means the opposite of its name.** `remain_in_force` is 残存効力 —
whether an *already repealed* law keeps residual effect through transitional
provisions. Live statutes carry `False`. Reading it as the in-force flag marks
the entire live corpus as repealed; the Local Autonomy Act, plainly in force,
reports `False`. The verdict here is computed from `current_revision_status`
(CurrentEnforced) and `repeal_status`, and both inputs are returned so the
reasoning can be checked.

**Page size counts sentences, not statutes.** `limit=3` came back as three
sentences inside one Act; `limit=30` spanned five. The parameter is named
`max_sentences` for what it actually bounds, and the response reports both counts
so a thin page is not mistaken for a thin corpus.

**Point-in-time has a floor.** e-Gov keeps revisions only from 2017-04-01. An
earlier `as_of` is refused with that explanation rather than falling back to
current text.

Legislation only — e-Gov holds no case law.

## Run

```
python server.py                                  # stdio
python server.py --transport http --port 8060     # http://127.0.0.1:8060/mcp
```

Search in Japanese: 会社法, 電気事業, 労働契約.

MIT licensed. Part of [arthurlegal-mcp](https://github.com/beerbottle90/arthurlegal-mcp).
