# ArthurLegal MCP

**Fifteen jurisdictions of legal research behind one MCP endpoint.**

Türkiye - Netherlands - Poland - Austria - Ireland - Finland - Spain - United
Kingdom - European Union - Japan - Azerbaijan - Germany - legal scholarship -
signed resource contracts - GLEIF entity identity.

104 tools, one hosted endpoint, no authentication:

    https://arthurlegal-mcp.fly.dev/mcp

Anyone with the URL can call every tool. Self-hosting? Deploy your own copy (see
[Indexes](#indexes)) and use `https://<your-app>.fly.dev/mcp` instead.

## Use it directly

This is the endpoint the ArthurLegal assistant packages use. You can also connect
it to any MCP client on its own (Streamable HTTP transport).

**Claude (claude.ai or Claude Desktop):** Settings -> Connectors -> Add custom
connector. Name: `ArthurLegal MCP`, URL: `https://arthurlegal-mcp.fly.dev/mcp`.
Leave the OAuth fields empty.

**Claude Code:**

    claude mcp add --transport http arthurlegal https://arthurlegal-mcp.fly.dev/mcp

**Clients that take a JSON config with remote servers** (Cursor, VS Code and others):

```json
{ "mcpServers": { "arthurlegal": { "url": "https://arthurlegal-mcp.fly.dev/mcp" } } }
```

**Clients that only launch local (stdio) servers:** bridge with
`npx -y mcp-remote https://arthurlegal-mcp.fly.dev/mcp` as the command.

**Check it is up:**

    curl -s -X POST https://arthurlegal-mcp.fly.dev/mcp       -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream'       -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'

Then call the `status` tool from your client to see which jurisdictions are loaded.

The hosted endpoint is provided as is, without an uptime guarantee. Your search
queries reach this server and the official upstream sources it queries, and are
sent to the embeddings provider when semantic search runs. Do not put client names
or other confidential facts into queries; mask documents locally first (for
example with [Arthur Mask](https://github.com/beerbottle90/arthur-mask)).

Türkiye (`tr_`) is the largest backend: Yargıtay/Danıştay/BAM case law, AYM,
Uyuşmazlık, all legislation types, Resmî Gazete, and eight regulators (EPDK,
Rekabet, SPK, BDDK, KVKK, BTK, GİB, Sigorta Tahkim) behind one
`tr_kurum_karari_ara` interface, plus a locally indexed
archive for `tr_semantik_ara`. Source: the `ArthurLegalTR/` folder.

`tr_resmi_gazete_tara`, `tr_resmi_gazete_fihrist` and `tr_mevzuat_ara` also take a `konu` filter
(`enerji`, `rekabet`, `vergi`, `icra`): a local, network-free topic triage over titles that finds
items whose titles never mention the topic (in the labelled gazette corpus the topic word appears
in only 10% of `icra` items). In legislation it also reads the names of the laws an omnibus act
amends -- "Bazi Kanunlarda Degisiklik Yapilmasina Dair Kanun" says nothing, Law 7531 amends the
Enforcement and Bankruptcy Code -- and an omnibus act whose names cannot be read is never dropped.
It is a pre-filter, not a guarantee, and it was measured separately per source: gazette
out-of-fold sensitivity 92-100% per topic; on 60 unseen legislation titles vergi 4/4, icra 2/2,
enerji 0/3. Every response says how many items it dropped, `esik=0` disables it, and `status`
reports the model and both measurements. Do not use it for publication verification.

## Turkish land-registry parcel tools (`tkgm_`)

`tkgm_` tools work on a parcel file **the user downloads themselves** from TKGM's Parsel Sorgu
(GeoJSON/KML): plane-projected area and edge lengths, a to-scale A4 SVG sketch, an OSM map, format
conversion (DXF/KML/CSV), route-corridor intersection, 2026 title-deed fee and revolving-fund
tariff lookups (every figure stored with a verbatim source quote), and a bridge from the parcel to
the statutes and case law to check with the `tr_` tools. This server **never calls TKGM**: Parsel
Sorgu's terms of use forbid direct or indirect access to its web services (art. 3) and commercial
use of its output (art. 4). Parsel Sorgu exports vertices rounded to 5 decimals (~1.1 m), so the
tools report the resulting area uncertainty instead of presenting a rounding artefact as a
cadastral discrepancy. On this hosted endpoint file paths are refused (parcel text is passed in
`icerik`) and the tools that write files, fetch elevation data or read title records are disabled;
run `tkgm-mcp/server.py` locally over stdio for those. Source: the `tkgm-mcp/` folder.

## Tool naming

Every tool carries its jurisdiction as a prefix -- `nl_` `pl_` `at_` `ie_` `fi_`
`es_` `uk_` `eu_` `jp_` `gleif_` `az_` `de_` `scholar_` `contracts_`. Across the underlying servers
`get_act` means five different things and `search_legislation` three, so the
prefix is what keeps a Spanish question from being answered with Finnish law.

`status` reports every jurisdiction at once: which backends loaded, how many
documents each has indexed, how many are vectorised, and whether semantic
search is live. A backend that fails to load is announced there rather than
quietly returning nothing -- "no results" and "not searched" are different
answers.

## Indexes

Five jurisdictions are searched from a local SQLite index (FTS5 + vectors); the
rest are queried upstream and reranked in memory. The indexes are built on a
workstation and baked into the image at `baked/<jurisdiction>.db`:

    python crawl.py --embed          # in each jurisdiction directory
    python build_bundle.py --target fly --out fly-app
    flyctl deploy

Crawling is a maintenance task, not something a booting container does. Baking
the databases in means a machine is never healthy-but-empty, and the corpus can
never drift away from the code that was tested against it. `start.sh` still
falls back to crawling when no baked index is present, and says which it used:
`indexes present: N/5`.

## Configuration

| Name | Kind | Purpose |
|---|---|---|
| `EMBEDDINGS_API_KEY` | secret | Voyage AI key. Required for semantic search. |
| `EMBEDDINGS_URL` | env | `https://api.voyageai.com/v1/embeddings` |
| `EMBEDDINGS_MODEL` | env | `voyage-4-lite` -- multilingual, 1024-dim |
| `DE_ELI_URL` | env | German backend; `off` to disable |

A document holds exactly one vector (`vecs.doc_id` is the primary key), so
changing `EMBEDDINGS_MODEL` does not add a second vector -- the next embedding
run overwrites the old one. Until it does, queries find no vectors for the new
model and semantic search reports itself off. That is deliberate: bge-m3 and
voyage-4-lite are both 1024-dimensional, so mixing them would raise nothing and
rank by distances computed across two unrelated vector spaces.

Without a key the server still answers, on BM25 and fuzzy matching, and every
response says `semantic: "off"` rather than passing a keyword match off as a
conceptual one.

---

Source: [github.com/beerbottle90/arthurlegal-mcp](https://github.com/beerbottle90/arthurlegal-mcp)
