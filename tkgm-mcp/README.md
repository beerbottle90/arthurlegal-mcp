# tkgm-mcp

Tapu-kadastro parsel araçları: ölçü, **ölçekli SVG kroki**, biçim dönüşümü ve parselden
hukuka köprü. Yalnız standart kütüphane; `pip install` yok. Birleşik `arthurlegal-mcp`
ucunda araçlar `tkgm_` önekiyle görünür.

## Bu sunucu TKGM'ye bağlanmaz

TKGM Parsel Sorgulama Uygulaması Kullanım Koşulları (v1.0):

- **md. 3** — uygulamanın çalışması için ihtiyaç duyulan web servislerine TKGM'den izin
  almaksızın doğrudan ve/veya dolaylı yöntemlerle erişimde bulunulamaz.
- **md. 4** — sunulan bilgi ve belgeler bilgilendirme amaçlıdır, resmî işlemlerde
  kullanılamaz, ticari amaçla kullanılması yasaktır.

Bir MCP sunucusunun o servisleri kullanıcı adına çağırması "dolaylı erişim"dir. Bu yüzden
veri yolu kullanıcıdan geçer:

1. `baglanti` Parsel Sorgu bağlantısını üretir; kullanıcı **kendi tarayıcısında** açar.
2. Kullanıcı parseli sorgular, arayüzün dışa aktarma seçeneğiyle GeoJSON/KML indirir.
3. `parsel_oku` dosyayı okur; sonrası (`geometri`, `kroki`, `disa_aktar`, `hukuk_koprusu`)
   tamamen yereldir.

Belgelenmemiş Parsel Sorgu uç adresleri bu depoda **bilerek yoktur**. Canlı sorgu ve
toplu/güzergâh dökümü için izinli yol, *Tapu ve Kadastro Verilerinin İşlenmesi ve Elektronik
Ortamda Yapılacak İşlemler Hakkında Yönetmelik* (RG 08.06.2022/31860) m. 6 ve 10 uyarınca TKGM
ile protokoldür; talebi Veri Paylaşımı Üst Komisyonu karara bağlar (m. 33-34). (2015 tarihli
"Verilerin Paylaşımı Hakkında Yönetmelik" yürürlükteki külliyatta yoktur.) `tkgm_kaynak.canli_durum`
o kapıyı tutar, gövdesi TKGM'nin vereceği servis sözleşmesine göre yazılacaktır.

Malik, şerh, beyan ve rehin bilgisi Parsel Sorgu'da yoktur; kullanıcı bunları kendi
e-Devlet / Web Tapu oturumundan alır. Bu sunucu hiçbir oturuma girmez.

## Çalıştırma

    python server.py                                 # stdio — dosya okur/yazar
    python server.py --transport http --port 8060    # paylaşılan kip — dosya erişimi kapalı

| Ortam değişkeni | Anlamı |
| --- | --- |
| `TKGM_CIKTI` | Çıktı klasörü (varsayılan `~/ArthurLegal/tkgm`) |
| `TKGM_DOSYA_ERISIMI` | `1`/`0`: taşıma türünden bağımsız olarak dosya erişimini aç/kapat |
| `TKGM_IZIN_BELGESI`, `TKGM_SERVIS_URL` | TKGM izni alındığında canlı kaynak kapısı (henüz gövdesiz) |

HTTP taşımasında dosya yolu kabul edilmez: kimlik doğrulamasız bir uçta yol alan araç,
sunucunun diskini internete açar. Orada parsel `icerik` ile metin olarak verilir, kroki
`svg` alanında döner.

## Tarayıcı arayüzü (ArthurLegal · Tapu)

    python server.py --ui        # http://127.0.0.1:8765 açılır: dosyayı bırak, kroki/harita/3D gör, Word föy indir
    python server.py --ui-ile    # MCP (stdio) + aynı süreçte arayüz: Claude'un okuttuğu parseller arayüzde de görünür

Arayüz ayrı bir API değildir; MCP araç listesini `POST /api/cagir` üzerinden açar. Yalnız 127.0.0.1'e
bağlanır, yabancı `Host` başlığını ve `X-Tkgm` başlığı taşımayan POST'ları reddeder (açık bir sekmedeki
başka bir sitenin bu porta istek atmasına karşı); dosya sunumu çıktı klasöründeki düz adlarla sınırlıdır.
Kroki `<img>` içinde, harita ve 3D `sandbox`'lı iframe içinde gösterilir. "Dosyalarım" sekmesi çıktı
klasörünü listeler — Claude oturumlarında üretilen dosyalar da orada görünür.

## Araçlar

| Araç | İş |
| --- | --- |
| `rehber` | Veri yolu, akış, kapsam dışı kalanlar |
| `baglanti` | Parsel Sorgu / Web Tapu / e-Devlet bağlantıları ve indirme adımları |
| `parsel_oku` | GeoJSON/KML → `ref`, öznitelik, hesap alanı, nitelik rejimi uyarıları |
| `geometri` | Alan, çevre, kenar boyları, semtler, iç açılar (grad) |
| `kroki` | A4, mm birimli, ölçekli SVG; komşu parsellerle |
| `harita` | OpenStreetMap altlıklı etkileşimli HTML harita (karoları kullanıcının tarayıcısı yükler) |
| `arazi_3d` | Copernicus GLO-30 üstüne parsel: kot, eğim, bakı + 3D HTML — yalnız yerel kipte |
| `koridor_kesisim` | Hat + genişlik → parsel başına kesilen alan ve eksen boyu, ölçülmüş hata payıyla |
| `parsel_foyu` / `portfoy_tablosu` | Word föyü (.docx) ve Excel dökümü (.xlsx) — yalnız yerel kipte |
| `tarife_ara` / `harc_hesapla` | 2026 tapu harcı + döner sermaye; her rakam `veri/tarife_2026.json`da alıntısıyla |
| `tapu_kaydi_oku` | Kullanıcının kendi aldığı tapu kaydı metni → şema; TCKN/IBAN/telefon/e-posta **ve malik adları** maskeli, yalnız yerel kipte |
| `disa_aktar` | GeoJSON, KML, DXF (R12, TM koordinatlı), CSV |
| `koordinat_donustur` | Coğrafi ↔ ITRF96 TM 3° / UTM 6° |
| `hukuk_koprusu` | Dayanak madde adresleri + hazır `tr_mevzuat_ara` / `tr_ictihat_ara` çağrıları |

## Tapu kaydında ne maskelenir

`tapu_kaydi_oku` iki katmanda maskeler. Kalıp katmanı sağlama doğrular: NVİ basamakları
tutmayan on bir haneli sayı (yevmiye no) maskelenmez, tutan maskelenir; IBAN mod-97'den,
VKN ise yalnız satırında "vergi kimlik no"/"VKN" geçiyorsa. Ad katmanı NLP kullanmaz —
ayrıştırıcı adın hangi dizge olduğunu `Malik:` satırından zaten bilir, bu yüzden ad
`{{MALİK-nn}}` olur ve şerh/rehin satırları dâhil kayıdın her yerinde aynı etiketi alır.

Yanıt, maskelenMEYENİ de adıyla sayar: şerhteki üçüncü kişi ve şirket adları (alacaklı
banka, kiracı, mahkeme), adres, doğum tarihi. Bunlar için tam takma adlandırma
[Arthur Mask](https://github.com/beerbottle90/arthur-mask)'in işidir; bu sunucu yalnız
standart kütüphane kullandığı için onu içe almaz.

## Dış ağ çağrıları

TKGM'ye: **hiç**. Tek dış çağrı `arazi_3d`nin Copernicus DEM karosudur (AWS açık veri, adres
beyaz listeli, ~1-2 MB) ve yalnız yerel kipte açıktır: kimlik doğrulamasız paylaşılan uçta
isteyen herkes sunucuya bant genişliği harcatabilirdi. Harita ve 3D sayfalarındaki OSM karoları
ile three.js/Leaflet betiklerini sunucu değil, dosyayı açan tarayıcı yükler.

## Dışa aktarımın hassasiyeti

Parsel Sorgu köşe koordinatlarını **5 ondalığa** yuvarlayarak verir (~1,1 m ızgara). On üç köşeli
bir kent parselinde bu tek başına ±25 m² alan belirsizliğidir. `geometri` ve `parsel_oku`
belirsizliği kapalı formülle hesaplar ve "hesap alanı – kayıtlı alan" farkının bunun içinde
kalıp kalmadığını söyler; kroki yaklaşık boyları "≈" ile ve tek ondalıkla yazar.

## Ölçü nasıl yapılır

Kadastro alanı projeksiyon düzleminde hesaplanır: köşeler ITRF96 / TM 3° dilimine
(DOM 27…45, EPSG:5253–5259) Krüger serisiyle (n⁴) izdüşürülür, alan Gauss formülüyle çıkar.
Gidiş-dönüş hatası mikrometre mertebesindedir. ED50 datum dönüşümü yapılmaz.

Kroki milimetre birimlidir: %100 ölçekle yazdırıldığında başlıktaki ölçekte çıkar. Yine de
**aplikasyon krokisi veya röperli kroki yerine geçmez**; hesap alanı ile dosyadaki alan
arasındaki fark bir işarettir, hata tespiti değildir.

## Token disiplini

Görsel ve dışa aktarım dosyaya yazılır; modele yol ve birkaç satır özet döner (kroki yanıtı
~120 token). Yanıtlar sıkıştırılmış JSON'dur. Parseller içerik özetinden türeyen `ref` ile
anılır; geometri her çağrıda yeniden gönderilmez.

## Test

    python -m unittest discover -s tests

İzdüşüm ezber bir koordinata karşı değil bağımsız hesaplara karşı sınanır: meridyen yayı
sayısal integralle, konformluk ve ölçek katsayısı sonlu farkla.
