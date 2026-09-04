# resourcecontracts-api

Dependency-free Python client + MCP server for **ResourceContracts.org**, the
open repository of **petroleum and mining contracts** (NRGI / CCSI / World Bank /
OpenOil / ALSF), for use by the ArthurLegal assistants (Claude Projects and Copilot
Studio). Signed oil, gas and mining contracts are a primary source for energy and
natural-resources legal work.

> Status (2026-07-24): **working.** The full chain — grouped search → metadata →
> full text → curated annotations → taxonomy — is verified live against
> `api.resourcecontracts.org`. The core client uses only the Python standard
> library and runs on a stock Python 3.9+. Built to mirror the sibling
> [`eqanun-api`](../eqanun-api) so both plug into the same Copilot Studio / Claude
> setups.

- **Data:** 5,125 published contracts, 107 countries, 141 resources (commodities).
- **API:** public, unauthenticated, GET-only, officially documented, no bot guard.
- **License:** contract content is CC BY-SA 4.0 (attribution + share-alike).
- **Sister DB:** `api.openlandcontracts.org` (land / agriculture / forestry) —
  same API shape; pass `api_base=OPENLAND_API_BASE`.

## Why this exists

ResourceContracts.org publishes the actual signed contracts *and* expert
**annotations** that pinpoint key clauses (arbitration, governing law,
stabilization, fiscal terms, environmental and local-content obligations). That
annotation layer is exactly what a legal-research agent wants: it turns a
258-page scanned PSA into a handful of cited key terms. This package exposes
search + metadata + full text + annotations as MCP tools and a CLI.

## Install / requirements

None. Pure standard library, Python 3.9+. (The MCP protocol is implemented
directly in stdlib — the `mcp` SDK is **not** required.)

## Library

```python
from resourcecontracts import ResourceContractsClient

c = ResourceContractsClient()

# Azerbaijan hydrocarbons contracts
hits = c.search(country="az", resource="Hydrocarbons", per_page=5)
print(hits["total"], hits["facets"]["year"])
for r in hits["results"]:
    print(r["id"], r["year_signed"], r["name"][:60])

# Metadata + curated key clauses for one contract
meta = c.get_metadata(5158)          # Absheron offshore PSA, 258 pages
ann  = c.get_annotations(716)        # -> arbitration, governing law, term, ...

# Full text, a page-window at a time (one PDF page per source request)
text = c.get_fulltext(5158, start_page=1, page_count=3)
```

## CLI

```bash
python3 -m resourcecontracts search --country az --resource Hydrocarbons -n 10
python3 -m resourcecontracts search "gas" --country az
python3 -m resourcecontracts count --country az
python3 -m resourcecontracts meta 5158
python3 -m resourcecontracts annotations 716
python3 -m resourcecontracts text 5158 --out absheron.txt   # whole contract -> file
python3 -m resourcecontracts countries | head
python3 -m resourcecontracts resources
python3 -m resourcecontracts categories
```

## MCP server

Dependency-free MCP over two transports (see `resourcecontracts/mcp_server.py`).

```bash
# local stdio (desktop MCP client config)
python3 server.py

# remote, no-auth Streamable HTTP — connect http://<host>:<port>/mcp
python3 server.py --transport http --host 0.0.0.0 --port 8000
```

Env fallbacks: `RC_MCP_TRANSPORT`, `RC_MCP_HOST`, `RC_MCP_PORT`.

### Tools

| Tool | Purpose |
|---|---|
| `search_contracts` | grouped search with filters (country, resource, year, type, company, annotated, …); returns facets + hits |
| `count_contracts` | count matching a query/filters |
| `get_contract_metadata` | structured metadata for one contract id |
| `get_contract_text` | full text, a window of PDF pages (start_page + page_count); reports total_pages / next_page |
| `get_contract_annotations` | expert-curated key-clause annotations |
| `list_countries` / `list_resources` / `list_years` | taxonomy with counts |
| `list_annotation_categories` | annotation category taxonomy (key clause types) |

### Public HTTPS in one command

```bash
./run-public.sh
```

On **Windows** (PowerShell 5.1 or 7) use the native script instead — `&&` and
`.sh` do not work in PowerShell:

```powershell
.\run-public.ps1
```

Both boot the HTTP server and a Cloudflare quick tunnel, then print a
`https://<random>.trycloudflare.com/mcp` URL — paste it into the Copilot Studio
MCP connector (`copilot-studio/mcp-connector.swagger.json` → `host`), **No auth**.
Quick-tunnel URLs change per run; for a stable URL use a named Cloudflare tunnel
(see `copilot-studio/RUNBOOK.md`). Override the port with `RC_PORT` (`$env:RC_PORT`
on Windows).

## Copilot Studio connectors

- `copilot-studio/mcp-connector.swagger.json` — custom **MCP** connector
  (Streamable HTTP). Set `host` to your public MCP host. Gives the agent all
  tools, including cleaned full text.
- `copilot-studio/rest-connector.swagger.json` — direct **REST** connector over
  `api.resourcecontracts.org` (no hosting needed). Covers search + metadata +
  annotations + taxonomy; full text comes back with `<br />` markup (no
  server-side HTML→text), so prefer the MCP connector for reading text.

## Smoke test

```bash
python3 examples/smoke_test.py
```

Exercises search → count → metadata → full text → annotations → taxonomy against
the live API.

## Verification (2026-07-24)

- `/contracts/count` → 5,125.  `country_code=az` → 21 (Gold, Hydrocarbons, Solar).
- `country_code=az&resource=Hydrocarbons` → 16; first hit id 5158 = *Total E&P
  Absheron B.V., … PSA, 2009* (258 pages).
- `/contract/716/annotations` → 31 curated clauses (year, arbitration, …).
- 141 resources (Hydrocarbons 1906), 84 annotation categories.
- MCP stdio: `initialize` + `tools/list` (9 tools) + `tools/call` verified.

## Governance

Public CC BY-SA 4.0 contract content — **attribute** ResourceContracts.org
(NRGI / CCSI) and preserve share-alike. Cache aggressively (contracts change
rarely); send an identifying User-Agent. For Azerbaijani-law matters these are a
primary source; route to it for petroleum/mining contract questions and
cite the contract name + source_url from metadata.
