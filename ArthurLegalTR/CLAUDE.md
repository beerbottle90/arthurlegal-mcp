# CLAUDE.md — ArthurLegalTR

Turkish legal research MCP server. Standard library only (+ optional `pypdf`).

## Run / test

```bash
python server.py                                  # stdio
python server.py --transport http --port 8080     # POST /mcp, GET /health
python tests/test_offline.py                      # 12 offline tests, no network
python crawl.py --source kvkk,bddk --embed        # build data/index.db
```

## Layout

- `server.py` — tool table + instructions. Every tool is a thin `_wrap()` over an adapter function.
- `sources/<kurum>.py` — one upstream each; exposes `SOURCE = Source(key, label, kind, notes, search, get, crawl, …)`.
  Add a regulator = add a module + its name to `sources.MODULES`. Nothing else changes.
- `net.py` — urllib client: cookies, token bucket, charset fallback, IRI quoting, TLS cipher override.
- `textx.py` — HTML/PDF → text, Turkish lower/fold, pagination, excerpts.
- `retrieval.py` — vendored FTS5 + trigram + vector index (RRF). `_dotless_variants` is the only local change.
- `mcpcore.py` — vendored JSON-RPC MCP core. Do not edit; upstream is arthurlegal-mcp.
- `aes_min.py` — pure-Python AES-CBC (FIPS-197 vectors in `__main__`) for EKAP v2 request signing only.
- `crawl.py` — builds the local index; stamps `subject = <kurum key>` so `semantik_ara` can filter.

## Rules that are not style

- **Citations come from the record.** Every result carries `citation`; adapters build it from upstream
  fields, never from inference. Do not "improve" a citation by guessing a missing number.
- **Unavailable ≠ empty.** A failed upstream returns `error` / `upstream_blocked` / `unavailable`. Never
  convert that into an empty `results` list.
- **Bedesten rate limit is real** (10 req / 30 s per IP). `bedesten_ictihat.BUCKET` is shared with mevzuat.
  Don't add parallel fan-out over Bedesten.
- **No paid keys.** KVKK/BDDK/Sigorta Tahkim are parsed directly; if a source truly needs CAPTCHA solving
  (TÜRKPATENT) the adapter says so and points to the working route instead.
- **Windows console is cp1254.** Run tests with `PYTHONIOENCODING=utf-8`.

## Known gaps (2026-09-05)

- KİK: EKAP v2 changed signing headers to `X-Ekap-Sec-1..6`; implemented, but the API now answers HTTP 500.
  Key lives in `KIK_R8FACT`. `generateSecurityHeaders` sits in chunk `1959.*.js`; the `environment`
  object (with `r8fact`) is not a string literal in any of the 54 shell chunks nor in the
  module-federation remotes (`/f_ihale-araclari/remoteEntry.js`) — it is probably injected at runtime.
  Next step would be a browser capture of one signed request to compare header values.
- Aggregator integration: `server.py` exposes module-level `TOOLS` and `_t_status`; arthurlegal-mcp
  loads this directory first so its `retrieval.py` (dotless-ı) is the cached copy for every backend.
- Sayıştay: upstream WAF answers 418 to DataTables POSTs for every client. Reported as `upstream_blocked`.
- TÜRKPATENT: no public decision database; research portal is reCAPTCHA-gated. Adapter is a router only.
- EPDK: only the "Kurul Kararları" trees per market (structural decisions); individual licence acts are not listed there.
