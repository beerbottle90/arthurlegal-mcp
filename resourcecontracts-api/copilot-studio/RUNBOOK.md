# Copilot Studio runbook — ResourceContracts connectors

Two ways to give a Copilot Studio agent access to
ResourceContracts.org. Same pattern as the `eqanun-api` runbook.

## Option A — REST connector (no hosting) — fastest

Import `rest-connector.swagger.json` as a **custom connector**. It calls
`api.resourcecontracts.org` directly from Power Platform; **No auth**. Covers
search, metadata, annotations and taxonomy. Full contract text is available but
comes back with `<br />` markup (no server-side HTML→text) — good enough for
snippets, not for clean reading.

1. Power Platform → Custom connectors → New → Import an OpenAPI file.
2. Upload `rest-connector.swagger.json`. Host is already `api.resourcecontracts.org`.
3. Security: **No authentication**. Create, then Test the `SearchContracts` action
   (`country_code=az`, `resource=Hydrocarbons`).
4. Add the connector's actions to your agent's tools.

## Option B — MCP connector (host the server) — full capability

Gives the agent the full tool set incl. **cleaned, paginated full text** and a
tidy tool surface. You host `server.py` (this repo) and expose `/mcp` over HTTPS.

1. Run a public HTTPS endpoint. Quick/dev:
   ```bash
   ./run-public.sh          # prints https://<random>.trycloudflare.com/mcp
   ```
   Production: a **named Cloudflare tunnel** or Tailscale Funnel for a **stable**
   URL (quick-tunnel hostnames change every run), or deploy `server.py`
   (`--transport http`) to any container host / VM and put HTTPS in front.
2. Edit `mcp-connector.swagger.json` → set `host` to your public host
   (hostname only, no scheme, no `/mcp`).
3. Power Platform → Custom connectors → Import `mcp-connector.swagger.json`.
   Security: **No authentication**. Create.
4. In Copilot Studio, add the MCP connector to the agent. It should list:
   search_contracts, count_contracts, get_contract_metadata, get_contract_text,
   get_contract_annotations, list_countries, list_resources, list_years,
   list_annotation_categories.

## Notes

- **No auth** is intentional: the upstream API is public and this server adds
  none. Do not expose write endpoints (there are none). Consider IP-allowlisting
  the tunnel/origin for a production deployment.
- **Attribution:** content is CC BY-SA 4.0 (NRGI / CCSI). When the agent quotes a
  contract, cite the contract name + `source_url` from metadata.
- **Caching:** contracts change rarely — cache metadata/text/annotations
  aggressively to cut latency and be polite to the upstream.
- **Stability:** for a stable public URL, prefer a named Cloudflare tunnel over a
  quick tunnel; the quick-tunnel URL is best-effort and rotates per run.
