# arthurlegal-mcp

**Ten jurisdictions of legal research behind one MCP endpoint.**

🇳🇱 Netherlands · 🇵🇱 Poland · 🇦🇹 Austria · 🇮🇪 Ireland · 🇫🇮 Finland · 🇪🇸 Spain ·
🇦🇿 Azerbaijan · 🇩🇪 Germany · 🌍 legal scholarship · 🌍 signed resource contracts

62 tools, no authentication.

## Why one endpoint

Ten separate connectors meant ten addresses to register, and — because the
servers were reachable only through ephemeral tunnels — ten addresses to
re-enter by hand every time the host restarted. One endpoint means one.

Consolidation was measured before it was adopted: routing a search through the
aggregator instead of straight at its backend costs **+3 ms on an 88 ms query**.
Nine of the ten backends are loaded *in-process*, so their handlers are called
directly with no network in between. de-eli runs on FastMCP and is proxied over
loopback inside the container.

## Every tool carries its jurisdiction

`nl_` `pl_` `at_` `ie_` `fi_` `es_` `az_` `de_` `scholar_` `contracts_`

This is not cosmetic. Across the underlying servers `get_act` means five
different things, `search_legislation` three and `search_acts` three. Merging
them unprefixed would route a Spanish question to a Finnish server and answer
confidently with the wrong country's law.

## One health answer

`status` reports every jurisdiction at once: which backends loaded, how many
documents each has indexed, what date range was crawled, and whether semantic
search is live. A backend that fails to load is **announced** — its jurisdiction
is reported unavailable rather than quietly returning nothing, because "no
results" and "not searched" are different answers and only one of them is safe
to act on.

## Semantic search

Set `EMBEDDINGS_URL` to any OpenAI-compatible `/v1/embeddings` endpoint and the
dense channel turns on across all jurisdictions. Verified locally with Ollama and
`bge-m3`: a Turkish query separated conceptually related documents at 0.70 cosine
from unrelated ones at 0.41. Without it, search degrades to BM25 plus fuzzy
matching and every response says which ran, so a keyword ordering is never
mistaken for a conceptual one.

The model must be multilingual for cross-language questions. The server cannot
verify that and does not pretend to.

## Indexes

Four of the jurisdictions cannot be searched at their source at all — the Dutch
case-law API has no free-text parameter, the Irish Statute Book publishes no
search endpoint — so those servers keep local SQLite indexes. On a cold start the
crawl runs in the background; searches work as soon as it lands, and report an
empty index plainly until then.

## Run it locally

```sh
python server.py                                  # stdio
python server.py --transport http --port 8900     # http://127.0.0.1:8900/mcp
```

## Licence

MIT for the server code. The underlying legal data belongs to its publishers and
carries their terms.

---

Part of [ArthurLegal](https://github.com/beerbottle90/ArthurLegal).
