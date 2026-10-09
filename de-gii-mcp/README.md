# de-gii-mcp

German federal statutes from [gesetze-im-internet.de](https://www.gesetze-im-internet.de), norm by
norm. Standard library only, no index, no key.

    python server.py                                 # stdio
    python server.py --transport http --port 8095    # http://127.0.0.1:8095/mcp

In the hosted endpoint the tools are `de_norm_getir` and `de_gesetz_ara`, next to the NeuRIS
tools of de-eli.

## Why it exists

NeuRIS (rechtsinformationen.bund.de) is the official successor, but its test-phase dataset
holds about 5,500 acts and does not yet contain the core codes. On 2026-10-09
`/v1/legislation?abbreviation=` returned 0 for BGB, HGB, StGB, ZPO, StPO, AO, UrhG, GWB and
InsO. gesetze-im-internet.de, run by the Federal Ministry of Justice with juris, carries nearly
all current federal law as one XML file per act.

## Tools

| Tool | Does |
|---|---|
| `norm_getir(kanun, norm, page)` | One norm's current consolidated text: heading, text, the act's Stand line, a citation (`§ 823 BGB`) and the norm's own page (`/bgb/__823.html`, `/gg/art_1.html`). |
| `gesetz_ara(sorgu, limit)` | Title search over the site's table of contents (`/gii-toc.xml`), to find the page name of an act whose abbreviation you do not know. |

`kanun` takes an abbreviation (`BGB`), a page name (`ao_1977`) or a full title. A few
abbreviations whose page name differs are mapped (`AO` → `ao_1977`, `WEG` → `woeigg`, `SGB V`
→ `sgb_5`); a guessed page name is checked against the act's own abbreviation, so a wrong
guess fails instead of answering with another act.

## Limits

- The text is consolidated and not authentic; only the Bundesgesetzblatt is. Every result says so.
- No historical versions: the site publishes the current text only.
- Acts are fetched on first use and the last six are kept in memory.
