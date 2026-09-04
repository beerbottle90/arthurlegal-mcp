# Copilot Studio — e-qanun custom connector runbook

Two ways to give a Copilot Studio agent access to Azerbaijani legislation
(e-qanun.az, the Ministry of Justice's official legal-acts database). Both are
**no authentication**. Pick one (or both).

| | Option A — REST (direct) | Option B — MCP (server) |
|---|---|---|
| Hosting needed | **None** (Power Platform calls the public API) | Yes — the MCP server must be reachable over HTTPS |
| Full act text | ❌ not available (Referer-guarded, HTML) | ✅ `get_act_fulltext`, paginated |
| Search + metadata + taxonomy | ✅ | ✅ |
| File | `rest-connector.swagger.json` | `mcp-connector.swagger.json` |
| Best for | fastest path, zero infra | full capability incl. full text |

If you have no hosting, Option A works immediately for search and metadata.
Choose Option B when you need full act text.

Copilot Studio is one supported target, not the only one: the same MCP server
works with any MCP client (Claude Desktop and the claude.ai connector UI, among
others) — see the repository README for the stdio and HTTP setups.

---

## Option A — REST custom connector (no hosting)

`api.e-qanun.az` is public, unauthenticated, and not UA/Referer-gated, so Power
Platform can call it straight from the cloud.

1. Power Platform / Copilot Studio maker portal → **Custom connectors** →
   **New custom connector** → **Import an OpenAPI file**.
2. Upload `rest-connector.swagger.json`. Name it e.g. `e-qanun REST`.
3. **Security** tab → Authentication type: **No authentication**.
4. **Definition** tab: 5 actions are present — `SearchActs` (`/getDetailSearch`),
   `CountActs` (`/detailNameCount`), `GetAct` (`/framework/{id}`), `ListTypes`
   (`/all-type`), `ListSections` (`/sections`).
5. **Create connector**, then **Test** tab → create a connection (no
   credentials) → try `SearchActs` with `name = mülki məcəllə`, `title = true`,
   `statusId = 1`, `length = 20`, `orderColumn = 8`, `array = ` (leave empty).
   Expect `totalCount` and a `data` array.

   > `length`, `orderColumn` and `array` are **mandatory upstream** — omitting
   > any one of them makes `/getDetailSearch` return HTTP 500. They are marked
   > required in the connector file so Power Platform always transmits them.

6. In Copilot Studio, open your agent → **Tools** →
   **Add a tool** → select the connector's actions. Add usage guidance so the
   agent verifies status (`statusName`) and dates from `GetAct`.

To restrict a search to one act type, pass `array` = comma-separated type ids
from `ListTypes` — e.g. `array = 73` returns only Constitutional Court decisions
(464), `73,87` adds Supreme Court Plenum decisions (578 total). `CountActs`
ignores `array`; for a type-filtered count use `SearchActs` with `length = 1` and
read `totalCount`.

Note: to read full act text, take `htmlUrl` from `GetAct` — but the static host
requires a same-site `Referer` and returns MS-Word HTML (large acts run to
megabytes) that a raw connector cannot convert. For full text, use Option B.

---

## Option B — MCP custom connector (full capability)

Connects the `eqanun-api` MCP server (`search_acts`, `count_acts`, `get_act`,
`get_act_fulltext`, `list_types`, `list_sections`). No auth.

### 1. Expose the server over HTTPS — fastest path

Copilot Studio runs in the cloud and requires **HTTPS**, so it cannot reach
`localhost`. The one-command way (free Cloudflare quick tunnel), **run from the
repository root, not from this folder**:

```bash
cd /path/to/eqanun-api
./run-public.sh
```

On Windows (the default execution policy blocks unsigned scripts):

```powershell
cd C:\path\to\eqanun-api
powershell -ExecutionPolicy Bypass -File .\run-public.ps1
```

> These scripts use a `cloudflared` already on your PATH if there is one. If not,
> they will **download and run a third-party binary**, and they refuse to unless
> you opt in with `EQANUN_ALLOW_DOWNLOAD=1` — Cloudflare publishes no checksum
> file, so an automatic download **cannot be integrity-verified**. Installing
> cloudflared yourself (`brew install cloudflared`, `winget install
> Cloudflare.cloudflared`) is safer. Or skip the scripts: start the server with
> `python3 server.py --transport http --port 8000` and use your own tunnel.

It prints a public URL like `https://xxxx.trycloudflare.com`; your MCP endpoint
is that URL **+ `/mcp`**. Avoid ngrok's free tier — it injects a browser
interstitial page that breaks API/MCP clients.

Note: the HTTP transport listens with no authentication and permissive CORS. Do
not expose it any wider than you need to, and put it behind your own gateway if
the URL is not effectively private.

### 2. Stable URL (recommended once it works)

A quick tunnel is best-effort and its URL changes every run; the options below
keep one hostname across restarts.

Pick one, then set the connector host **once**. `<PORT>` below is the port your
server is actually on — 8000 by default, or whatever you set via `EQANUN_PORT`.
(`.tools/cloudflared` is bootstrapped by the first `run-public` run; otherwise
install cloudflared yourself.)

- **Named Cloudflare tunnel** (needs any domain on a free Cloudflare plan) —
  fast global edge, stable hostname:
  ```bash
  .tools/cloudflared tunnel login
  .tools/cloudflared tunnel create eqanun
  .tools/cloudflared tunnel route dns eqanun eqanun.yourdomain.com
  .tools/cloudflared tunnel run --url http://127.0.0.1:<PORT> eqanun
  ```
  Connector `host` = `eqanun.yourdomain.com`.

- **Tailscale Funnel** (no domain needed, stable `*.ts.net` URL): install
  Tailscale, then `tailscale funnel <PORT>`. Connector `host` = the printed
  `*.ts.net` host.

Both keep the same URL across restarts. To remove any dependence on your local
machine staying on, deploy the server to a small always-on host instead (any
VM/container with a public HTTPS name) — costs a little but removes the laptop
from the path.

### 3. Import the connector

1. Edit `mcp-connector.swagger.json`: replace the placeholder
   `host: "REPLACE-ME.example.com"` with your public host (no scheme, no path —
   e.g. `abc123.trycloudflare.com`). Keep `basePath: "/"`, the `/mcp` path, and
   `x-ms-agentic-protocol: mcp-streamable-1.0`.
2. Custom connectors → **New** → **Import an OpenAPI file** → upload it.
3. **Security** → **No authentication**.
4. **Create connector**.
5. In Copilot Studio → your agent → **Tools** → **Add a tool** →
   **Model Context Protocol** → pick this connector. Its six tools appear.

### 4. Verify the endpoint before importing (optional)

```bash
curl -s -X POST https://<your-host>/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Should list the six tools. (The server answers either `application/json` or SSE,
whichever the client accepts.)

---

## Governance and fair use

- This connector is read-only research over the official Ministry of Justice
  database: it searches, retrieves metadata, and reads act text. It performs no
  submission, filing, or messaging of any kind. If your agent has filing, email,
  or other external-submission tools, consider keeping them disabled on a
  research-only connector.
- What the source covers: legal acts — laws and codes, presidential fərman /
  sərəncam, Cabinet decisions, central executive-body and regulator acts, local
  self-government acts — including the officially published Constitutional Court
  decisions, Supreme Court Plenum decisions and Azerbaijan-related ECtHR
  judgments that the database carries as act types. There is no case-law search
  layer: no docket, party, judge or citation search, and no citator. Judicial
  documents are reached through the ordinary act chain (search → `get_act` →
  `get_act_fulltext`).
- Status and dates are the publisher's own labels. The agent should read
  `statusName` (Qüvvədədir / Ləğv olunmuş) and `acceptDate` / `effectDate` from
  `GetAct` / `get_act` and report them as published — there is no independent
  verification, no amendment history and no point-in-time versioning here, and
  `effectDate` is frequently `null`. Cite Azerbaijani legislation as a primary
  source and link back to the canonical `htmlUrl` on e-qanun.az.
- Identify your own deployment: set `EQANUN_USER_AGENT` so the server names you
  and gives the operator a contact route. Be a polite client — cache what you
  fetch, keep request rates modest, and do not mirror the corpus.
- If you intend to run this in production, prefer an official API arrangement
  from justice.gov.az (their Swagger is IP/referer-gated), and review
  e-qanun.az's own terms of use for yourself.
- **No warranty, and nothing here is legal advice.** The software is provided
  as-is under the MIT License; the assessment above is not a substitute for your
  own review of the source's terms.
