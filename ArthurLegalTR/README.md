# ArthurLegalTR

**Türk hukuku tek MCP ucunda: içtihat, mevzuat, 8 düzenleyici kurum, Resmî Gazete ve semantik arama.**

Yargıtay · Danıştay · BAM · yerel mahkeme · KYB · Anayasa Mahkemesi · Uyuşmazlık Mahkemesi ·
mevzuat (12 tür, madde ağacı, gerekçe) · Resmî Gazete · **Rekabet Kurumu · EPDK · SPK · BDDK ·
KVKK · BTK · GİB · Sigorta Tahkim Komisyonu**

25 araç. Standart kütüphane; tek opsiyonel bağımlılık `pypdf`. Ücretli arama anahtarı yok
(Brave / Tavily / OpenRouter gerekmez). Yerel SQLite indeks: FTS5 + trigram + vektör
(Voyage, OpenAI-uyumlu veya yerel Ollama).

## Neden

Said Sürücü'nün `yargi-mcp` / `mevzuat-mcp` çalışması Türk yargı kaynaklarının hangi uçtan
konuştuğunu ortaya koydu ([ATTRIBUTION.md](ATTRIBUTION.md)). Bu sunucu o bilgiyi alır, çerçeveyi
ve ücretli anahtarları bırakır, enerji/finans pratiğinin gerçekten atıf yaptığı sektör
düzenleyicilerini — EPDK Kurul kararları ağacı, SPK haftalık bültenleri, BDDK'nın iki karar
listesi, Sigorta Tahkim dergileri — ekler ve hepsinin arkasına, Türkçe sorunun
kelime paylaşmadığı kararı da bulabilen bir yerel indeks koyar.

## Kurulum

```bash
git clone https://github.com/beerbottle90/arthurlegal-mcp
cd arthurlegal-mcp/ArthurLegalTR
pip install pypdf          # opsiyonel ama PDF'ler için gerekli
python server.py           # stdio
```

Claude Desktop / Claude Code (`claude_desktop_config.json` ya da `claude mcp add`):

```json
{ "mcpServers": { "ArthurLegalTR": { "command": "python", "args": ["C:/…/ArthurLegalTR/server.py"] } } }
```

HTTP (claude.ai connector, Copilot Studio): `python server.py --transport http --port 8080` →
`POST /mcp`, `GET /health`. Kimlik doğrulama yoktur; `--host 0.0.0.0` bilinçli bir seçimdir.

## Araçlar

| Aile | Araçlar | Kaynak |
|---|---|---|
| İçtihat | `ictihat_ara` `ictihat_getir` `ictihat_semantik_ara` | Bedesten (5 mahkeme türü, 79 daire kodu) |
| AYM | `aym_ara` `aym_getir` | Kararlar Bilgi Bankası (norm + bireysel) |
| Uyuşmazlık | `uyusmazlik_ara` `uyusmazlik_getir` | kararlar.uyusmazlik.gov.tr |
| Mevzuat | `mevzuat_ara` `mevzuat_getir` `mevzuat_icindekiler` `mevzuat_madde_getir` `mevzuat_gerekce` `mevzuat_icinde_ara` | Bedesten mevzuat |
| Resmî Gazete | `resmi_gazete_fihrist` `resmi_gazete_getir` `resmi_gazete_tara` | resmigazete.gov.tr |
| Kurum | `kurum_karari_ara` `kurum_karari_getir` `kurum_listesi` `spk_bulten_icinde_ara` | 8 kurum, tek arayüz |
| Yerel indeks | `semantik_ara` `belge_getir` | `data/index.db` |
| Yardımcı | `hukuk_arastirma_rehberi` `status` | — |

Her sonuç `citation` taşır ve kayıttan üretilir; model esas/karar numarası uydurmaz. Erişilemeyen
kaynak `error` / `upstream_blocked` / `unavailable` döner — boş liste değil.

### Kurum arayüzü

```
kurum_karari_ara(kurum="epdk", query="lisanssız üretim", market="elektrik")
kurum_karari_ara(kurum="rekabet", query="enerji", decision_type="birlesme_devralma")
kurum_karari_ara(kurum="bddk", query="faaliyet izni", year=2026)
kurum_karari_ara(kurum="sigorta_tahkim", query="kasko", issue=66)
kurum_karari_ara(kurum="spk", year=2026)  →  spk_bulten_icinde_ara(bulten="2026/27", query="halka arz")
kurum_karari_getir(kurum="epdk", id="https://www.epdk.gov.tr/Detay/DownloadDocument?id=…")
```

Kuruma özel filtreler `kurum_listesi` ile görülür; `params={…}` ile geçilir.

## Yerel indeks ve semantik arama

Canlı kaynaklar indeks olmadan da çalışır. `semantik_ara` için:

```bash
python crawl.py --source kvkk,bddk,btk,rekabet,epdk,spk,sigorta_tahkim --embed
python crawl.py --source sigorta_tahkim --issues 1-66 --embed      # 16 yıl hakem kararı
python crawl.py --source spk --years 2023,2024,2025,2026 --embed
python crawl.py --source bddk,rekabet --fetch-text --embed         # karar PDF metinleri (yavaş)
```

Gömme ucu ortam değişkenleriyle seçilir; hiçbiri yoksa `127.0.0.1:11434` (Ollama, `bge-m3`) denenir:

| Değişken | Örnek |
|---|---|
| `EMBEDDINGS_URL` | `https://api.voyageai.com/v1/embeddings` |
| `EMBEDDINGS_MODEL` | `voyage-4-lite` (çok dilli, 1024) |
| `EMBEDDINGS_API_KEY` | Voyage / OpenAI anahtarı |

Vektör yoksa `semantik_ara` yine cevap verir ve `retrieval.semantic: "off"` der; anahtar kelime
eşleşmesini kavramsal eşleşme gibi sunmaz.

## Durum (2026-09-06)

| Kaynak | Durum |
|---|---|
| Bedesten içtihat + mevzuat (madde ağacı, gerekçe), AYM, Uyuşmazlık, RG, Rekabet, EPDK, SPK, BDDK, KVKK, BTK, GİB, Sigorta Tahkim | ✅ canlı uçta arama + getirme doğrulandı (2026-09-06) |
| KİK, Sayıştay, TÜRKPATENT, İSTAÇ | ❌ **kaldırıldı** — resmi uçları güvenilir cevap vermiyor (EKAP 500, Sayıştay WAF 418, reCAPTCHA, istac.org.tr DNS). Çalışıyormuş gibi gösterilmez; ayrıntı `docs/SOURCES.md` |

Ayrıntı ve endpoint'ler: [docs/SOURCES.md](docs/SOURCES.md).

## Dağıtım

**Üretim yolu: arthurlegal-mcp aggregator'ı.** Bu repo, `beerbottle90/arthurlegal-mcp`
içinde `tr_` önekiyle bir backend olarak yüklenir (`server.py` modül düzeyinde `TOOLS`
verir; aggregator `status`'u kendi durumuna katlar). Tek uç: `https://arthurlegal-mcp.fly.dev/mcp`
→ `tr_ictihat_ara`, `tr_kurum_karari_ara`, `tr_semantik_ara` … TR indeksi vektörleriyle birlikte (voyage-4-lite) imaja gömülür;
aggregator'ın `start.sh`'ı eksik vektör kalırsa Fly'daki anahtarla tamamlar.

Tek başına: `Dockerfile` + `fly.toml` (Fly.io, `fra`). İndeks imaja gömülür; açılan konteyner
asla crawl yapmaz. `flyctl secrets set EMBEDDINGS_API_KEY=…` semantik kanalı açar.

## Test

```bash
PYTHONIOENCODING=utf-8 python tests/test_offline.py     # ağ yok, 11 test
```

## Lisans

MIT. Üçüncü taraf bileşenler ve veri kaynakları için [ATTRIBUTION.md](ATTRIBUTION.md).
Çıktılar hukuki tavsiye değildir; her atıf birincil kaynaktan teyit edilmelidir.
