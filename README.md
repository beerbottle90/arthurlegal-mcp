---
title: ArthurLegal MCP
emoji: ⚖️
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# ArthurLegal MCP

**Ten jurisdictions of legal research behind one MCP endpoint.**

🇳🇱 Netherlands · 🇵🇱 Poland · 🇦🇹 Austria · 🇮🇪 Ireland · 🇫🇮 Finland · 🇪🇸 Spain ·
🇦🇿 Azerbaijan · 🇩🇪 Germany · 🌍 legal scholarship · 🌍 signed resource contracts

62 tools. Connect an MCP client to `https://<this-space>.hf.space/mcp` — no
authentication.

## Configuration

Set these in **Settings → Variables and secrets**. Only the first is normally
needed; the rest have working defaults.

| Name | Kind | Purpose |
|---|---|---|
| `EMBEDDINGS_API_KEY` | secret | Voyage AI key. Turns on semantic search. |
| `EMBEDDINGS_URL` | variable | `https://api.voyageai.com/v1/embeddings` |
| `EMBEDDINGS_MODEL` | variable | `voyage-4-lite` — multilingual, 1024-dim |
| `DE_ELI_URL` | variable | German backend; `off` to disable |

Leaving the key unset is safe: search still runs on BM25 + fuzzy matching, and
every response reports `semantic: "off"` rather than passing a keyword match off
as a conceptual one.

## Tool naming

Every tool is prefixed with its jurisdiction — `nl_` `pl_` `at_` `ie_` `fi_`
`es_` `az_` `de_` `scholar_` `contracts_`. Across the underlying servers
`get_act` means five different things and `search_legislation` three, so the
prefix is what keeps a Spanish question from being answered with Finnish law.

Call `status` for the health of every jurisdiction at once: which backends
loaded, how many documents each has indexed, and whether semantic search is
live.

## First boot

Indexes are crawled and vectorised in the background on first start (roughly
10–20 minutes). The server answers from the first second — direct document
fetches never need an index — and every search reports `indexed_documents` and
`vectorised_documents` so a thin result is never mistaken for a settled
question.

⚠️ A Space's disk is ephemeral: on the free tier the crawl repeats after a
restart. Attach a Storage Bucket to keep the indexes.

---

Source: [github.com/beerbottle90/arthurlegal-mcp](https://github.com/beerbottle90/arthurlegal-mcp)
