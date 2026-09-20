# Changelog

## 0.4.0 — 2026-09-20

- `mevzuat_ara` takes `konu` (`enerji` · `rekabet` · `vergi` · `icra`), `esik` and `max_pages`, and no
  longer needs a query: Bedesten lists by `types` and/or RG date range without one (verified live).
  That turns "which laws amended the İİK in the last two years" into a question that can be asked.
  - **Omnibus laws.** "Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun" says nothing about what it
    amends; Law 7531 amends the İİK and the HMK. When the title does not pass, the names of the
    amended laws are read from the first page of the text and scored. An omnibus law whose names
    cannot be read (request cap of 5 per call, fetch error, "İlgili Kanunlara işlenmiştir") is
    **never dropped** — it comes back with `konu_kaynak: "belirsiz"` and is counted in `konu_belirsiz`.
  - **Measured separately, not copied from the gazette numbers.** 187 Bedesten titles, 6 types,
    RG 2024-09…2026-09 (`arthurlegal-1.9.1-jev-edition/olc_mevzuat.py`). 127 of them are training
    examples of the gazette model, so that slice measures format transfer only: 26/26 positives
    kept, 3 false positives. On the 60 unseen titles: **vergi 4/4, icra 2/2, enerji 0/3** (two are
    the gazette golden set's known traps — solid fuels, street-lighting deductions — one is new:
    wind power monitoring), 2 false positives; without the omnibus pass vergi 3/4, icra 1/2. The
    schema, the guide, every response and `status` repeat these numbers. Do not rely on `konu`
    for energy in legislation; combine it with `query`.
- Fixed: **`ictihat_ara` / `ictihat_semantik_ara` with only `date_from` (or only `date_to`) were
  unfiltered.** Bedesten ignores a one-sided `kararTarihi` range without saying so: "işe iade" with
  `date_from=2025-01-01` returned 52,993 decisions, the same as with no date; with both bounds, 612
  (verified live 2026-09-20). `ictihat_semantik_ara` only takes `date_from`, so its date filter never
  worked. The adapter now fills the missing bound.
- Fixed: a one-sided RG date range was **silently ignored** by Bedesten — `rg_date_from="2024-09-01"`
  alone returned all 917 laws instead of 6 (verified live 2026-09-20; the same with `rg_date_to`
  alone). The adapter now fills the missing bound. Every earlier `mevzuat_ara` call that passed one
  bound got unfiltered results without being told.
- Fixed: `mevzuat_ara` allowed `page_size` up to 50, but Bedesten answers HTTP 400 above **20**
  ("Kayıt sayısı 20'den fazla olamaz"). Clamped; schema and docs corrected.
- `crawl.py --only-new` and `tazele.sh`: a refresh mode that skips refs already in the index
  *before downloading them*. `upsert` replaces a body and drops its vector, so re-running the EPDK
  crawl (first done with `--fetch-text`) would have overwritten 3,745 full texts with listing stubs.
  First refresh, 2026-09-20: +94 documents (rekabet 77, spk 14, bddk 2, epdk 1), 0 overwritten,
  19,404 vectors intact.
- `crawl.py --backfill-text`: BDDK, BTK and Rekabet were first indexed from their listings, so 13,313 of 19,498
  archive documents had a body equal to their title and `semantik_ara` searched titles, not decisions. The backfill
  fetches full text for exactly those rows (resumable; the vector of a changed row is dropped). 2026-09-20: BDDK
  964/964, BTK 1,897/1,904. "idari para cezası" occurs in the text of 419 BTK decisions and in the title of 9; "dolaylı pay sahipliği" in the text
  of 60 BDDK decisions and in the title of none.
  Rekabet is left title-only on purpose — its upstream search already matches inside the PDFs. The regulators'
  `notes` (what `kurum_listesi` returns) now say which side holds the text.
- `triyaj.hazirla()` validates `konu`/`esik` in one place, before any request; `status.triyaj`
  carries the legislation measurement.

## 0.3.0 — 2026-09-20

- `resmi_gazete_tara` and `resmi_gazete_fihrist` take a `konu` filter
  (`enerji` · `rekabet` · `vergi` · `icra`): a local, network-free topic triage
  that finds items whose titles never mention the topic. This is a capability
  the tools did not have — `query` matches literally, and in the labelled
  corpus the topic word appears in only 10% of `icra` items, 14% of `rekabet`,
  58% of `enerji`, 61% of `vergi`. "Konkordato Gider Avansı Tarifesi" is an
  enforcement matter that no `icra` query will ever return.
  - `query` is no longer required; `query` or `konu` is. Given both, they AND.
  - New `triyaj.py` — standard library only, no numpy, no network, no key, no
    second process. Character 3–5-grams + tf-idf + logistic regression, Platt
    calibrated, over a rule layer that only ever raises a score. Trained in
    `arthurlegal-1.9.1-jev-edition`; only inference ships here.
  - **It is a pre-filter, not a guarantee.** Out-of-fold sensitivity at the
    measured 0.20 threshold: enerji 97%, rekabet 100%, vergi 93%, icra 92% —
    and lowering the threshold does not recover the rest. Every response says
    how many items were dropped; `esik=0` disables the filter and returns the
    full list with scores. Do not use `konu` for publication verification.
  - `status` now reports the triage model's presence, threshold and measured
    sensitivity, so a missing model is visible before it is relied on.

## 0.2.0 — 2026-09-06

- Removed KİK, Sayıştay, TÜRKPATENT and İSTAÇ: their official endpoints do not answer reliably
  (EKAP 500 after the signing change, Sayıştay WAF 418, reCAPTCHA, istac.org.tr DNS). Eight regulators
  remain, each verified live for both search and fetch. `aes_min.py` went with KİK.
- `mevzuat_gerekce` now calls `/getGerekceContent` (was returning "Content bulunmadı").
- SPK archive years (2005–2024) and every Sigorta Tahkim heading spelling are parsed.
- TLS fallback for hosts whose chain the container CA store cannot verify (BDDK, Resmî Gazete).
- Full index: 19,4xx documents across 8 regulators, vectorised with voyage-4-lite.

## 0.1.0 — 2026-09-05

First cut. 25 tools, 17 source adapters, standard library + optional `pypdf`.

- Bedesten içtihat (Yargıtay, Danıştay, BAM, yerel, KYB) and mevzuat (12 türler, madde ağacı,
  gerekçe) on a shared rate-limit bucket.
- AYM Kararlar Bilgi Bankası, Uyuşmazlık Mahkemesi, Resmî Gazete fihrist (+ aralık taraması).
- Regulators behind one `kurum_karari_*` interface: Rekabet, EPDK (Kurul kararları ağacı,
  5 piyasa), SPK (haftalık bülten + bölüm içi arama), BDDK (iki liste), KVKK (karar özetleri),
  BTK, KİK, Sayıştay, GİB, Sigorta Tahkim (66 dergi), İSTAÇ (kurallar), TÜRKPATENT (rota).
- Local hybrid index (`crawl.py`): FTS5 + trigram + vectors; dotless-ı query expansion;
  `semantik_ara` with per-kurum filter; `status` reports coverage per kurum.
- No paid search keys anywhere.

Known gaps: KİK API answers 500 after the 2026-09 signing-header change; Sayıştay WAF 418;
TÜRKPATENT reCAPTCHA. See docs/SOURCES.md.
