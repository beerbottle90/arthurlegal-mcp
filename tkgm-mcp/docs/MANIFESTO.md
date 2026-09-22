# ArthurLegal Tapu: TKGM bağlantısı nasıl tasarlandı

## ArthurLegal

ArthurLegal, avukatların Claude ile çalışırken kullandığı açık kaynak bir araç takımıdır. Türk ve
yabancı mevzuat, içtihat, düzenleyici kurum kararları ve tapu kadastro araçları tek bağlantı
noktasında toplanır. Proje ticari değildir: bu bağlayıcı ücretsizdir ve kaynak kodu herkese açıktır.

## Bu bağlayıcı ne yapar

Kullanıcı sohbette bir yer, bir koordinat ya da il, ilçe, mahalle ile ada ve parsel numarası verir.
Bağlayıcı o tek parseli TKGM Parsel Sorgu'nun herkese açık verisinden getirir, alanını ölçer,
krokisini çizer ve ilgili mevzuat maddelerinin adresini ekler. Yer adları OpenStreetMap Nominatim'de,
onun kullanım koşuluna uygun olarak saniyede en fazla bir istekle aranır.

Malik, şerh, beyan ve rehin bilgisine erişmez, hiçbir oturuma girmez. Yalnız herkesin Parsel
Sorgu'da gördüğü veriyi okur. Sorgular diske yazılmaz; önbellek yalnız bellekte tutulur ve bir gün
içinde düşer (il, ilçe ve mahalle listeleri bir ay kalır).

## Nasıl tasarlandı

Bağlayıcı, TKGM sunucularına Parsel Sorgu'yu kullanan tek bir kişiden fazla yük bindirmeyecek
şekilde kuruldu. Aşağıdaki sınırlar bugünkü kodda, `tkgm_canli.py` dosyasının başında tanımlıdır ve
testlerle sınanır.

- **Tek sıra.** Bütün kullanıcıların istekleri tek kuyruktan geçer. TKGM'de aynı anda en fazla bir
  isteğimiz bulunur ve iki isteğin başlangıcı arasında en az 2 saniye vardır. Üst sınır dakikada en
  çok 30 istektir; bu sayı kullanıcı sayısıyla artmaz. Bir avukatın kendi bilgisayarına kurduğu
  sürümde aynı sınırlar o bilgisayar için geçerlidir.
- **Taşan istek gönderilmez.** Kuyrukta bekleme 20 saniyeyi aşacaksa istek TKGM'ye iletilmez, bizim
  sunucumuzda "yoğun" yanıtıyla geri çevrilir. Kuyruk en fazla 10 istek tutar; yoğunluk bizde kalır.
- **TKGM yavaşlayınca geri çekilir.** Yanıtlar 2 saniyeyi aşınca aralık kendiliğinden ikiye katlanır
  ve 16 saniyeye kadar açılır, yani dakikada yaklaşık 4 isteğe iner.
- **Durması istendiğinde durur.** 429 ya da 503 yanıtında en az bir dakika hiç istek gönderilmez,
  Retry-After başlığına uyulur, tekrarında bekleme 15 dakikaya kadar uzar. Üst üste üç bağlantı
  hatası da aynı sonucu doğurur. Erişim reddedilirse (401, 403) bağlayıcı 24 saat durur. Engel
  aşılmaya çalışılmaz: adres, kimlik ya da IP değiştirilmez.
- **Önce önbellek.** Aynı parsel bir gün boyunca TKGM'ye ikinci kez sorulmaz. Ada ve parsel
  listeleme uçları hiç kullanılmaz.
- **Toplu taramaya kapalı.** Her çağrı tek parsel içindir. Aynı adada ardışık parsel numaralarını ya
  da küçük bir alanda sık noktaları tarayan sorgular durdurulur. Günlük toplam 3.000 istekle
  sınırlıdır.
- **Açık kimlik.** İstekler `ArthurLegal-Tapu/<sürüm>` adıyla ve bu sayfanın adresiyle gider.
  Tarayıcı taklidi, çerez ya da yönlendirme izleme yoktur.
- **Kullanıcı bilir.** Her sohbette ilk canlı sorgudan önce kullanıcıya kısa bir onay kartı
  gösterilir: verinin kaynağı, bilgi amaçlı olduğu ve bu sınırlar.
- **Tek anahtar.** `TKGM_CANLI=0` ile bütün canlı istekler kapanır.

## Rakamlar nereden geliyor

Parsel Sorgu'nun web arayüzünde bir parseli bulmak; ilçe, mahalle, ada ve parsel listeleriyle
parselin kendisi için birkaç API çağrısı yapar ve haritanın karolarını yükler. Bağlayıcı aynı
sorguyu, listeler önbelleğe girdikten sonra tek çağrıyla yapar ve harita karosu indirmez.

Dakikada 30 çağrı, arayüzde art arda sorgu yapan bir iki kişinin API yükü kadardır. Aynı anda tek
istek bulunduğu için yanıt yarım saniye sürse bile TKGM'deki ortalama eşzamanlı yükümüz 0,25
isteğin altında kalır: zamanın en az dörtte üçünde TKGM'de hiç isteğimiz yoktur. İlk denemede
ölçülen yanıt süreleri 0,09 ile 0,31 saniye arasındaydı; bu sürelerle oran yüzde 8 dolayındadır.
Bir anda bin kişi sorsa da TKGM'ye giden, dakikada 30 istektir; kalanı kuyrukta bekler ya da bizde
geri çevrilir.

## TKGM Bilgi Teknolojileri Dairesi Başkanlığı'na

Bu sayfanın adresi isteklerimizin kimlik başlığında yer alır. Bağlayıcıyı sunucu kayıtlarınızda
gördüyseniz amacımız şudur: avukatların parsel bilgisine sohbet içinden, TKGM altyapısına yük
getirmeden ulaşabilmesini denemek. Hız, önbellek ya da kullandığımız uçlar hakkında bir öneriniz,
bir itirazınız ya da tercih ettiğiniz resmî bir kanal varsa konuşmaya açığız.

İletişim: Ertuğ Demir, ArthurLegal kurucu ortağı.
LinkedIn: https://www.linkedin.com/in/ertug-demir-arthurlegal/

## Kaynak kod

Bağlayıcının kodu: https://github.com/beerbottle90/arthurlegal-mcp/tree/master/tkgm-mcp
(canlı sorgu `tkgm_canli.py`, testleri `tests/test_canli.py`).
