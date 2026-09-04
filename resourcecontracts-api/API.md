# ResourceContracts.org API reference (verified)

Map of the public, unauthenticated REST API behind
[resourcecontracts.org](https://www.resourcecontracts.org) — the open repository
of petroleum and mining contracts published by the Natural Resource Governance
Institute (NRGI), the Columbia Center on Sustainable Investment (CCSI), the World
Bank, OpenOil and the African Legal Support Facility. Unlike e-qanun, the API is
**officially documented** (GitHub wiki) — this file records the endpoints and the
exact parameter semantics **verified live on 2026-07-24**.

- API base: `https://api.resourcecontracts.org` (JSON; GET only; no auth)
- Frontend: `https://www.resourcecontracts.org`
- Official docs: <https://github.com/NRGI/resourcecontracts.org/wiki/API>
- Sister database (same API shape): `https://api.openlandcontracts.org`
  (agriculture / land / forestry — [openlandcontracts.org](https://openlandcontracts.org))
- Content license: **CC BY-SA 4.0** (reuse with attribution + share-alike)

No authentication, no bot protection (no Cloudflare/captcha) — server-side
integration is straightforward. Send a descriptive `User-Agent` as good practice.

---

## Search — `GET /contracts/group`

The main search endpoint. (`/contracts/search` also exists but returned `[]` for
plain `q=` queries in testing; `/contracts/group` is the one the site uses and
the one that returns facets.)

Query parameters (confirmed semantics):

| Param | Values | Meaning |
|---|---|---|
| `q` | string | free-text query; empty browses all |
| `country_code` | ISO alpha-2, **lowercase** (e.g. `az`) | country filter |
| `resource` | exact taxonomy name (e.g. `Hydrocarbons`, `Gold`) | commodity filter |
| `year` | int | signature year |
| `contract_type` | e.g. `Production or Profit Sharing Agreement` | contract type |
| `document_type` | e.g. `Company-State Contract` | document class |
| `language` | e.g. `en` | contract language |
| `company_name` | string | company filter |
| `corporate_group` | string | corporate group filter |
| `annotation_category` | category name | restrict to an annotated category |
| `annotated` | `1` | only contracts with annotations |
| `recent` | `1` | recently published |
| `from` | int | pagination offset |
| `per_page` | int | page size |
| `sortby` / `order` | field / `asc`\|`desc` | best-effort server sort |
| `group` | `metadata` \| `text` \| `annotations` (or `\|`-joined) | sub-docs to embed per hit |

Example:

```
GET https://api.resourcecontracts.org/contracts/group?country_code=az&resource=Hydrocarbons&per_page=5&group=metadata
```

Response (facets + hits):

```json
{
  "total": 16,
  "country": ["AZ"],
  "year": [1998, 2006, 2009, 2014],
  "resource": ["Hydrocarbons"],
  "results": [
    {
      "id": 5158,
      "open_contracting_id": "ocds-591adf-...",
      "name": "Total E&P Absheron B.V., ..., Absheron Offshore ... PSA, 2009",
      "year_signed": "2009",
      "contract_type": ["Production or Profit Sharing Agreement"],
      "document_type": "Company-State Contract",
      "resource": ["Hydrocarbons"],
      "countries": [{"code": "AZ", "name": "Azerbaijan"}],
      "language": "en"
    }
  ]
}
```

Verified: `country_code=az` → 21 total (Gold, Hydrocarbons, Solar);
`+resource=Hydrocarbons` → 16; `contract_type=Production or Profit Sharing
Agreement` → 733; grand total `/contracts/count` → 5,125.

## Count — `GET /contracts/count`

Grand total of all published contracts as a bare integer, e.g. `5125`. For a
**filtered** count, run `/contracts/group` with `per_page=1` and read `total`.

## Contract metadata — `GET /contract/{id}/metadata`

Rich structured metadata (OCDS-flavoured):

```
GET https://api.resourcecontracts.org/contract/5158/metadata
```

Key fields: `name`, `identifier`, `number_of_pages`, `language`, `countries[]`,
`resource[]`, `published_at`, `government_entity[]`, `contract_type[]`,
`document_type`, `date_signed`, `year_signed`, `participation[]` (company, share,
is_operator, opencorporates_url), `project`, `concession[]`, `source_url`.

## Full text — `GET /contract/{id}/text?page={n}`

OCR / full text, **one PDF page per request** (`page` is the 1-based PDF page).

- The response `total` field is a per-response **record count (usually `1`)**,
  **not** the page count. The real page count is metadata `number_of_pages`.
- Shape: `{"total": 1, "result": [{"contract_id", "id", "text"}]}`.
- `text` carries `<br />` and HTML entities; strip to plain text
  (`resourcecontracts._html.html_to_text`). Scanned contracts contain OCR
  artefacts; bilingual acts interleave (e.g. Azerbaijani + English) line by line.

To read a whole contract, loop `page = 1 .. number_of_pages`. This client's
`get_fulltext(id, start_page=, page_count=)` returns a cleaned page window;
`page_count=None` fetches to the end (capped at `max_pages`).

## Annotations — `GET /contract/{id}/annotations`

Expert-curated key-clause extractions — the highest-value signal for legal
review. Shape: `{"total": 31, "result": [{category, category_key,
article_reference, page_no, text, quote, cluster, shapes[]}]}`. Not every
contract is annotated (`total` may be `0`).

**`page` gotcha:** the optional `?page={n}` filters to annotations located on
**PDF page n** (e.g. `716?page=1` → the 6 front-page items; `?page=2` → 0), it is
**not** result pagination. **Omit `page` to get all annotations** (716 → 31).
This client's `get_annotations(id)` omits it by default.

Related: `/contract/{id}/annotations/group`, `/contract/{id}/annotations/search`,
`/contract/{id}/annotations/download` (CSV), `/annotation/{id}`.

## Taxonomy / lookups

- `GET /contract/countries` → `{results: [{code, contract}]}` (per-country counts).
- `GET /contract/resources` → `{results: [{resource, contract}]}` — 141 resources;
  top: Hydrocarbons (1906), Timber/Wood (809), Gold (514), Copper (367).
- `GET /contract/years` → `{results: [{year, contract}]}`.
- `GET /contracts/annotations/category` → `{results: [ "Type of contract",
  "Arbitration and dispute resolution", "Governing law", ... ]}` — 84 categories.
- `GET /contracts/summary`, `/contract/attributes`, `/contract/country/resource`,
  `/contracts/metadata/download` (CSV) — additional aggregates / bulk export.

---

## Endpoint status matrix

| Endpoint | Method | Status | Purpose |
|---|---|---|---|
| `/contracts/group` | GET | ✅ verified | search + facets (main) |
| `/contracts/count` | GET | ✅ verified | grand total (5,125) |
| `/contract/{id}/metadata` | GET | ✅ verified | structured metadata |
| `/contract/{id}/text?page=` | GET | ✅ verified | full text (1 PDF page/req) |
| `/contract/{id}/annotations` | GET | ✅ verified | curated key clauses |
| `/contract/countries` | GET | ✅ verified | country counts |
| `/contract/resources` | GET | ✅ verified | resource counts (141) |
| `/contract/years` | GET | ✅ verified | year counts |
| `/contracts/annotations/category` | GET | ✅ verified | annotation taxonomy (84) |
| `/contracts/search` | GET | ⚠️ returns [] for `q=` | superseded by /contracts/group |
| `api.openlandcontracts.org` | GET | ➕ same shape | land/agri sister DB |

## Governance / legal note

Content is public, CC BY-SA 4.0 contracts — reuse requires **attribution** and
**share-alike**. For a corporate deployment: cache aggressively (contracts change
rarely), send an identifying User-Agent, and attribute ResourceContracts.org /
NRGI-CCSI. Azerbaijani petroleum, gas and mining contracts (21 as of recon,
incl. the Absheron PSAs) are a **primary** source for
energy legal teams; the curated *annotations* map directly to review topics (arbitration,
governing law, stabilization, fiscal terms, environmental / local-content).
