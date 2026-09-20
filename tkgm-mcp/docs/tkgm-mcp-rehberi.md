# Tapu-Kadastro — tkgm MCP (Kullanım Rehberi)

> ✅ **Custom MCP backend VAR (`tkgm-mcp` v0.2.0).** Parsel ölçüsü, **ölçekli kroki**, harita ve
> 3D arazi, güzergâh-koridor kesişimi, parsel föyü/portföy tablosu, harç-masraf hesabı, tapu
> kaydı yapılandırma ve **parselden hukuka köprü**. Araç öneki **`tkgm_`**.
>
> ⛔ **TKGM'YE BAĞLANMAZ.** Bu bir eksik değil, tasarımdır — aşağıda §1.
>
> **Neden var:** Parsel Sorgu bir parselin nerede olduğunu gösterir; dava dosyasının istediği
> şeyleri göstermez: kenar boyları ve açıları, dosyaya girecek ölçekli bir kroki, koridorun
> parselden kestiği alan, tapu alanıyla geometrinin tutup tutmadığı, ve "bu nitelikte bir
> taşınmazda hangi kanuna bakılır". Bu sunucu, kullanıcının getirdiği parsel dosyasından
> sonrasını yapar.

---

## 0. Bağlantı bilgileri

| Alan | Değer |
|------|-------|
| **Tam özellik (önerilen)** | Yerel stdio: `python server.py` — dosya okur, görsel/rapor dosyası yazar |
| **Birleşik uç** | `https://arthurlegal-mcp.fly.dev/mcp` — yalnız dosya sistemi gerektirmeyen araçlar; parsel `icerik` ile metin olarak verilir, tapu kaydı aracı ÇALIŞMAZ (kişisel veri) |
| **Araç öneki** | `tkgm_` |
| **Auth** | Yok |
| **Sunucu kaynağı** | `github.com/beerbottle90/arthurlegal-mcp/tree/master/tkgm-mcp` |
| **Çıktı klasörü** | `~/ArthurLegal/tkgm` (`TKGM_CIKTI` ile değişir) |

> ⚠️ `tkgm_` araçları görünmüyorsa `status` çağır: backend yüklenmemiş olabilir. Araç yokken
> parsel ölçüsü, kroki ya da harç tutarı **uydurma**.

---

## 1. Veri yolu — parsel dosyası nereden gelir (ÖNCE BUNU OKU)

TKGM Parsel Sorgulama Uygulaması Kullanım Koşulları: **md. 3** — uygulamanın web servislerine
TKGM'den izin almaksızın *doğrudan ve/veya dolaylı* erişilemez; **md. 4** — çıktılar bilgi
amaçlıdır, resmî işlemde kullanılamaz, **ticari amaçla kullanılamaz**. Bir MCP sunucusunun o
servisleri kullanıcı adına çağırması dolaylı erişimdir. Bu yüzden:

1. `tkgm_baglanti` Parsel Sorgu bağlantısını üretir; **kullanıcı kendi tarayıcısında** açar.
2. Kullanıcı idari sorguyla (İl › İlçe › Mahalle, Ada, Parsel) parseli bulur; haritada parsele
   tıklar, açılan bilgi kutusunun köşesindeki **⋮ (üç nokta)** menüsünden **GeoJSON** (ya da KML)
   olarak indirir. Komşu parseller gerekiyorsa onlara da tek tek tıklayıp indirir.
3. `tkgm_parsel_oku(dosya=…)` dosyayı okur ve bir **`ref`** döndürür. Sonraki bütün araçlar `ref` ister.

**Yapma:** ada/parsel numarasından geometri üretme; "sorguladım" deme; kullanıcıdan TKGM/e-Devlet
şifresi isteme; toplu parsel indirme öner (yüzlerce parsellik güzergâh dökümü, TKGM ile
**protokol** ister: Tapu ve Kadastro Verilerinin İşlenmesi ve Elektronik Ortamda Yapılacak
İşlemler Hakkında Yönetmelik, RG 08.06.2022/31860, m. 6 ve 10).

---

## 2. Araçlar

| Araç | İş | Dönen |
|---|---|---|
| `tkgm_rehber` | Veri yolu ve akış özeti | metin (~250 token) |
| `tkgm_baglanti` | Parsel Sorgu / Web Tapu / e-Devlet bağlantıları, indirme adımları | bağlantılar |
| `tkgm_parsel_oku` | GeoJSON/KML → `ref`, öznitelik, hesap alanı, nitelik rejimi uyarıları | kısa JSON |
| `tkgm_geometri` | Alan, çevre, kenar boyları, semtler, iç açılar (grad) | kısa JSON |
| `tkgm_kroki` | A4, mm birimli **ölçekli SVG**; `komsular` ile çevre parseller | dosya yolu |
| `tkgm_harita` | OpenStreetMap altlıklı etkileşimli HTML harita | dosya yolu |
| `tkgm_arazi_3d` | Copernicus GLO-30 üstüne parsel; kot, eğim, bakı özeti + 3D HTML | özet + dosya yolu |
| `tkgm_koridor_kesisim` | Hat + genişlik → parsel başına kesilen alan, eksen boyu | tablo + CSV |
| `tkgm_parsel_foyu` | Tek parsel için Word föyü (kroki + ölçü + hukuki notlar) | .docx yolu |
| `tkgm_portfoy_tablosu` | Çok parsel için Excel dökümü | .xlsx yolu |
| `tkgm_harc_hesapla` | Tapu harcı + TKGM döner sermaye bedeli; her kalem kaynağıyla | kısa JSON |
| `tkgm_tapu_kaydi_oku` | Kullanıcının kendi aldığı tapu kaydı METNİ → şema, işaretler, çapraz kontrol | kısa JSON |
| `tkgm_disa_aktar` | GeoJSON / KML / DXF (R12, TM koordinatlı) / CSV | dosya yolu |
| `tkgm_koordinat_donustur` | Coğrafi ↔ ITRF96 TM 3° / UTM 6° | koordinatlar |
| `tkgm_hukuk_koprusu` | Dayanak madde adresleri + hazır `tr_` çağrıları | kısa JSON |

**Token disiplini.** Görsel ve raporlar **dosyaya** yazılır, yanıtta yol ve birkaç satır özet
döner. `inline=true` yalnız dosya yazılamayan birleşik uçta ya da kullanıcı içeriği açıkça
istediğinde. Kullanıcıya dosya yolunu ver; SVG/HTML içeriğini sohbete dökme.

---

## 3. Tipik akışlar

**Tek parsel, dava dosyası.** `parsel_oku` → `geometri` → komşuları da okut → `kroki(ref, komsular)` →
`hukuk_koprusu(konu=…, ref=…)` → köprünün önerdiği `tr_mevzuat_ara` / `tr_ictihat_ara` çağrılarını
çalıştır → `parsel_foyu`. Eğim/kot tartışması varsa (kamulaştırma değeri, taşkın yapı) `arazi_3d`.

**Portföy / due diligence.** Bütün dosyaları `parsel_oku` ile okut → `portfoy_tablosu` → dikkat
çekenlerde (büyük alan farkı, özel rejim uyarısı) tek tek föy.

**Güzergâh / proje sahası.** Parseller okutulur → `koridor_kesisim(refs, hat, genislik_m)` →
CSV. Sonuçtaki `sayisal_hata_yuzde_en_kotu` ölçülmüş hata payıdır; bedel hesabına girecek alan
için yine de harita mühendisi ölçüsü gerekir.

**İşlem masrafı.** `harc_hesapla(islem, bedel)`. Tutarı yazarken aracın döndürdüğü `kaynak` ve
`alinti` alanlarını da aktar; tarife her yıl değişir.

---

## 4. Disiplin — bu verinin söyleyebildiği ve söyleyemediği

- **Parsel Sorgu verisi bilgi amaçlıdır.** Malik, şerh, beyan, rehin **içermez**. Bunlar için
  kullanıcı kendi e-Devlet / Web Tapu oturumundan belge alır; metnini `tapu_kaydi_oku`ya verir.
  Sunucu hiçbir oturuma girmez.
- **Kroki resmî belge değildir.** Aplikasyon krokisi ve röperli kroki yerine geçmez; mahkemeye
  sunulacaksa "bilgi amaçlı, TKGM Parsel Sorgu verisinden üretilmiştir" kaydıyla ek yapılır.
- **Alan farkı bir İŞARETTİR.** Hesap alanı ile tapu alanı arasındaki fark tek başına hata
  tespiti değildir; düzeltme 3402 s. K. m. 41 yoluna ve kadastro müdürlüğü kayıtlarına tabidir.
- **3D arazi 30 m'lik yüzey modelidir (DSM):** bina ve ağaç yüksekliklerini içerir; küçük kent
  parsellerinde eğim kabadır. Keşif ve harita mühendisi ölçüsünün yerini tutmaz.
- **`hukuk_koprusu` adres verir, metin vermez.** Madde metnini `tr_mevzuat_*` ile çekmeden
  alıntılama; yanıttaki `teyit` alanı "YOK" diyorsa adres ezberdendir, önce doğrula. Bu kural
  süs değildir: modülün ilk taslağı 5403 m. 8/İ'deki sınırdaş önalım fıkrasını yürürlükte
  sanıyordu (7255 s. K. ile 2020'de kaldırıldı); TMK 733/1 de 24.12.2025'te değişti (7571 s. K.
  m. 35 — cebrî artırmada önalım kullanılamaz).
- **Tapu kaydı kişisel veridir (KVKK).** Araç T.C. kimlik no'yu maskeler ve hiçbir şeyi saklamaz;
  sen de malik adını gereksiz yere tekrarlama, başka araçlara taşıma.
- **Koordinat sistemi.** Ölçü ITRF96 / TM 3° düzlemindedir (DOM 27-45). ED50 (eski paftalar)
  datum dönüşümü yapılmaz; ED50 koordinatı verilirse bunu söyle.

---

## 5. Kapsam dışı (şimdilik)

| İstenen | Durum |
|---|---|
| Ada/parselden canlı sorgu, toplu indirme | TKGM protokolü gerekir (§1); kod tarafında kapı hazır, gövde yok |
| Malik/şerh bilgisini sunucunun çekmesi | Yapılmaz — yalnız kullanıcının kendi oturumundan aldığı belge |
| İmar durumu | Belediyelerin e-imar sistemleri ayrı ayrıdır; kapsamda değil |
| UYAP e-Satış ilanları | Ayrı çalışma; uçlar kullanıcının oturumundan kalibre edilmeden bağlanılmaz |
