# Changelog

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
