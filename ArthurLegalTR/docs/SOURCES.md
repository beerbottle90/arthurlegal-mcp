# Sources — what each adapter talks to, and what it cannot do

Verified live on 2026-09-05 unless stated. "Live" = queried upstream at tool
time; "Local" = crawled into `data/index.db` for `semantik_ara`.

| key | Kurum | Upstream | Method | Live | Local | Citation shape |
|---|---|---|---|---|---|---|
| `ictihat` | Yargıtay, Danıştay, BAM, yerel hukuk, KYB | `bedesten.adalet.gov.tr/emsal-karar` | JSON POST, base64 HTML/PDF | ✅ | — | `Yargıtay 9. Hukuk Dairesi, E. 2023/1, K. 2024/2, 01.02.2024` |
| `aym` | Anayasa Mahkemesi (norm + bireysel) | `*kararlarbilgibankasi.anayasa.gov.tr/api/core/public/search` | JSON POST | ✅ | — | `AYM, E. 2007/63, K. 2009/152, 2009-11-05 (RG 2010-04-15/27553)` |
| `uyusmazlik` | Uyuşmazlık Mahkemesi | `kararlar.uyusmazlik.gov.tr` | ASP.NET postback, PDF | ✅ | — | `Uyuşmazlık Mahkemesi, E. 2026/348, K. 2026/356, 15/06/2026` |
| `mevzuat` | 12 mevzuat türü, madde ağacı, gerekçe | `bedesten.adalet.gov.tr/mevzuat` | JSON POST | ✅ | — | `<Ad> (Kanun No. 6446, RG 30.03.2013/28603)` |
| `resmi_gazete` | Resmî Gazete fihrist | `resmigazete.gov.tr/eskiler/yyyy/mm/yyyymmdd.htm` | HTML (cp1254) | ✅ | — | `RG 05.09.2026, S. 33361` |
| `rekabet` | Rekabet Kurulu | `rekabet.gov.tr/tr/Kararlar`, `/Karar?kararId=` → PDF | HTML list + PDF | ✅ | ✅ | `Rekabet Kurulu, 27.11.2025 tarih ve 25-44/1086-615 sayılı karar` |
| `epdk` | EPDK Kurul kararları | `/Detay/Icerik/3-0-39-3/son-kurul-kararlari/elektrik` (+dogalgaz, petrol, lpg, denetim) → `POST /Detay/GetFastAccessList {fId}` → `/Detay/DownloadDocument?id=` | HTML tree + JSON + PDF/DOCX | ✅ | ✅ | `EPDK, 18.06.2026 tarihli ve 14681 sayılı Kurul Kararı (RG 20.06.2026/33286)` |
| `spk` | SPK haftalık bülten | `/spk-bultenleri/<yyyy>-yili-spk-bultenleri?s=N` → `spk.gov.tr/data/<id>/<yyyy>-<n>.pdf` | HTML list + PDF | ✅ | ✅ | `SPK Bülteni 2026/27, A. DUYURU VE İLKE KARARLARI` |
| `bddk` | BDDK Kurul kararları | `/Mevzuat/Liste/55` (RG'de yayımlanan), `/56` (yayımlanmayan) → `/Mevzuat/DokumanGetir/<id>` | HTML list + PDF | ✅ (title) | ✅ | `BDDK, 06.08.2026 tarih ve 11548 sayılı Kurul Kararı` |
| `kvkk` | KVK Kurulu karar özetleri | `/Icerik/5406/kurul-karar-ozetleri?page=N` (36 pages) → `/Icerik/<id>/<yyyy>-<no>` | HTML | ✅ (recent pages) | ✅ | `KVK Kurulu, 08/08/2024 tarih ve 2024/1361 sayılı karar özeti` |
| `btk` | BTK Kurul kararları | `btk.tr/api/content/board-decisions` | JSON GET + PDF | ✅ | ✅ | `BTK, 03.08.2026 tarih ve 2026/İK-THD/186 sayılı Kurul Kararı` |
| `kik` | Kamu İhale Kurulu | `ekapv2.kik.gov.tr/b_ihalearaclari/api/KurulKararlari/*` | signed JSON POST | ⚠️ 500 | — | `KİK, 12.04.2025 tarih ve 2025/UH.II-1801 sayılı karar` |
| `sayistay` | Sayıştay | `sayistay.gov.tr/Kararlar*/DataTablesList` | CSRF + DataTables POST | ⚠️ WAF 418 | — | `Sayıştay Temyiz Kurulu, <tarih> tarih ve <no> tutanak no.lu karar` |
| `gib` | GİB özelgeleri | `gib.gov.tr/api/gibportal/mevzuat/ozelge/list` | JSON POST (HTML inline) | ✅ | — | `GİB, 31.12.2025 tarih ve 62030549-125[6-2024]-1742807 sayılı özelge` |
| `sigorta_tahkim` | Sigorta Tahkim Komisyonu | `sigortatahkim.org/content/CmsFiles/karardrgs<N>.pdf` (1–66) | PDF, regex split | ✅ (per issue) | ✅ | `Sigorta Tahkim Komisyonu, 15.06.2026 Tarih ve K-2026/343230 Sayılı Hakem Kararı (Hakem Karar Dergisi S. 66)` |
| `istac` | İSTAÇ kuralları | `istac.org.tr/tr/kurallar` → rule PDFs | PDF | ✅ | ✅ | `İSTAÇ Tahkim Kuralları (v3, 2020), <başlık>` |
| `turkpatent` | TÜRKPATENT | — | — | ❌ | — | router only (reCAPTCHA; YİDK decisions not published) |

## Details worth knowing

**Bedesten** (ictihat + mevzuat). One IP gets ~10 requests per 30 s; the shared
token bucket spaces requests 3.5 s apart and honours `Retry-After`. Result
rows carry no text — `ictihat_getir` per decision. Search syntax: bare words
AND, `"phrase"`, `+must`, `-not`, `AND/OR/NOT`, no wildcards. Mevzuat search
defaults to the title; `search_in="fulltext"` sends `phrase` + `basliktaAra=false`.
Date filters are converted to the UTC boundaries Turkey's UTC+3 implies.

**EPDK**. No search API; the portal search needs reCAPTCHA v3. The Kurul
kararları trees (67 categories for elektrik, 18–19 for the others, 3 for
denetim) are fetched category by category and cached for six hours. Documents
come as PDF or DOCX; DOCX is unpacked from `word/document.xml` without a
library. The old guesses `3-0-0-94` / `3-0-0-122` in earlier ArthurLegal notes
are *yönetmelikler* and *YEKDEM* pages, not decisions.

**SPK**. Decisions are only in the weekly bülten PDF. Sections split on
`A. …`, `B. …` headings; the crawler stores one document per section with the
bülten date parsed from the cover.

**KVKK**. Listing pages are server-rendered with `page=N`; each card links
`/Icerik/<id>/<yyyy>-<no>`. Live search scans the newest pages only; the full
archive is a crawl.

**BDDK**. Both lists are single pages (~1,000 and ~900 links) with the date and
decision number inside the title `(dd.mm.yyyy - nnnn) …`. Live search is on
titles; `--fetch-text` crawls the PDFs.

**Sigorta Tahkim**. Issue 4 is `karardergisisayi4.pdf`, 57–61 are
`revizekd<N>.pdf`, the rest `karardrgs<N>.pdf`. Decisions start with
`dd.mm.yyyy Tarih ve K-yyyy/n Sayılı (İtiraz) Hakem (Heyeti) Kararı`.

**KİK**. EKAP v2 signs every request. The reference implementation (2026-05)
used `X-Custom-Request-*` headers; the bundle shipped in 2026-09 uses
`X-Ekap-Sec-1..6` (guid, IV, timestamp, HTTP method, path, all AES-CBC with
`environment.r8fact`). Implemented; the API currently returns HTTP 500 with
either scheme, which is reported as an error, not as "no decisions".

**Sayıştay**. The WAF in front of `DataTablesList` answers 418 to POSTs from
every client including browsers (confirmed upstream, 2026-05). Reported as
`upstream_blocked: true`.

**TÜRKPATENT**. `/yidk-kararlari` is a 404 shell; the research portal requires
a reCAPTCHA token per request. The adapter returns the manual portal URL and
the working judicial route (Yargıtay 11. HD decisions on YİDK acts, via
`ictihat_ara`).

## Not included (and why)

- Reklam Kurulu (Ticaret Bakanlığı): site returned 500 on every probe.
- KGK (Kamu Gözetimi Kurumu): decision list is loaded client-side; not reverse-engineered yet.
- SEDDK: no public decision listing found.
- EPDK licence registries (`apigateway.epdk.gov.tr/*LisansiSorgula`): Swagger-documented, useful, but a
  registry rather than decisions — a natural next adapter.
