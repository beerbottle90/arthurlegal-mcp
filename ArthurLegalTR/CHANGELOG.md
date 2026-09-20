# Changelog

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
