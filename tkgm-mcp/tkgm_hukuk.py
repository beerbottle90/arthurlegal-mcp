"""tkgm_hukuk — parselden hukuka köprü: hangi uyuşmazlıkta hangi dayanağa bakılır,
ve onu doğrulamak için hangi `tr_` aracı hangi argümanla çağrılır.

Bu modül hüküm METNİ taşımaz, yalnız adres taşır. Madde numaraları başlangıç
noktasıdır; mevzuat değişir, burada donmuş bir metin alıntılanırsa er geç
yürürlükten kalkmış bir hüküm "kanun böyle diyor" diye sunulur. Metin her
seferinde `tr_mevzuat_*` ile çekilir.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

DOGRULAMA = ("Dayanaklar ADRESTİR, metin değil. Alıntılamadan önce: tr_mevzuat_ara(number=…) → "
             "tr_mevzuat_icindekiler → tr_mevzuat_madde_getir. Süreler ve içtihat için "
             "önerilen tr_ictihat_ara sorgularını çalıştırın; belirtilen süreyi teyitsiz yazmayın.")


def _m(no: str, ad: str, maddeler: str) -> Dict[str, str]:
    return {"mevzuat_no": no, "ad": ad, "maddeler": maddeler}


def _i(sorgu: str) -> Dict[str, Any]:
    return {"arac": "tr_ictihat_ara", "args": {"query": sorgu}}


KONULAR: Dict[str, Dict[str, Any]] = {
    "tapu_iptal_tescil": {
        "baslik": "Tapu iptali ve tescil (yolsuz tescil)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu", "1023, 1024, 1025; Devletin sorumluluğu 1007"),
                    _m("6100", "HMK", "12 (taşınmazın aynına ilişkin kesin yetki)")],
        "parsel_verisi": "Ada/parsel ve taşınmazın bulunduğu yer yetkili mahkemeyi belirler; "
                         "`gittigi_parseller` doluysa parsel ifraz/tevhit görmüştür, dava güncel parsele yöneltilir.",
        "ictihat": [_i('"tapu iptali ve tescil" +"yolsuz tescil"'),
                    _i('"TMK 1023" +"iyiniyet"')],
    },
    "ecrimisil": {
        "baslik": "Ecrimisil (haksız işgal tazminatı)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu", "995"),
                    _m("2886", "Devlet İhale Kanunu", "75 (Hazine taşınmazları)")],
        "sure": "5 yıllık zamanaşımı YİBK 25.05.1938, 29/10'a dayandırılır — sorguyla teyit edin.",
        "parsel_verisi": "İşgal edilen kısmın alanı krokiden/keşiften gelir; paydaşlar arasında "
                         "intifadan men koşulu aranır.",
        "ictihat": [_i('"ecrimisil" +"zamanaşımı" +"beş yıl"'),
                    _i('"ecrimisil" +"intifadan men"')],
    },
    "ortakligin_giderilmesi": {
        "baslik": "Ortaklığın giderilmesi (izale-i şüyu)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu", "698, 699"),
                    _m("6100", "HMK", "4 (sulh hukuk mahkemesinin görevi)"),
                    _m("6325", "Hukuk Uyuşmazlıklarında Arabuluculuk Kanunu",
                       "18/B (dava şartı arabuluculuk)")],
        "parsel_verisi": "Aynen taksim mümkün mü sorusu alan, cephe ve imar/5403 asgari "
                         "büyüklüklerine bağlıdır; `geometri` çıktısı bilirkişi öncesi ön fikir verir.",
        "ictihat": [_i('"ortaklığın giderilmesi" +"aynen taksim"')],
    },
    "kamulastirma_bedel": {
        "baslik": "Kamulaştırma bedelinin tespiti ve tescil",
        "dayanak": [_m("2942", "Kamulaştırma Kanunu",
                       "4 (irtifak), 8 (satın alma), 10 (bedel tespiti-tescil), 11 (değer esasları), "
                       "12 (kısmen kamulaştırma), 27 (acele)")],
        "parsel_verisi": "Kısmi kamulaştırmada kalan kısmın alanı ve biçimi değer kaybı "
                         "tartışmasının temelidir.",
        "ictihat": [_i('"kamulaştırma bedeli" +"emsal"'),
                    _i('"kısmen kamulaştırma" +"değer azalışı"')],
    },
    "kamulastirmasiz_el_atma": {
        "baslik": "Kamulaştırmasız el atma (fiilî / hukuki)",
        "dayanak": [_m("2942", "Kamulaştırma Kanunu", "geçici 6, ek 1 (AYM iptallerini kontrol edin)")],
        "parsel_verisi": "El atılan kısmın alanı ve imar planındaki kullanım kararı görev "
                         "(adli/idari yargı) ayrımını belirler.",
        "ictihat": [_i('"kamulaştırmasız el atma" +"hukuki el atma"')],
    },
    "kadastro_itiraz": {
        "baslik": "Kadastro tespitine itiraz / kadastro öncesi nedene dayalı dava",
        "dayanak": [_m("3402", "Kadastro Kanunu",
                       "11 (askı ilanı), 12 (kesinleşme ve 10 yıllık hak düşürücü süre), 22 (yenileme)")],
        "sure": "Askı ilanı süresi ve m. 12/3'teki on yıllık hak düşürücü süre — madde metninden teyit edin.",
        "ictihat": [_i('"3402" +"12/3" +"hak düşürücü"')],
    },
    "alan_farki": {
        "baslik": "Hesap alanı ile tapu alanı farkı / sınır uyuşmazlığı",
        "dayanak": [_m("3402", "Kadastro Kanunu",
                       "41 (düzeltme — başlık 5304 s. K. ile değişti; ölçü, tersimat ve hesap hataları)"),
                    _m("4721", "Türk Medeni Kanunu", "719 (sınırların belirlenmesi)")],
        "parsel_verisi": "`geometri` aracındaki fark_m2 / fark_yuzde yalnız İŞARETTİR: Parsel Sorgu "
                         "verisi bilgi amaçlıdır, düzeltme talebi kadastro müdürlüğü kayıtlarına dayanır.",
        "ictihat": [_i('"3402" +"41. madde" +"yüzölçümü"')],
    },
    "onalim": {
        "baslik": "Yasal önalım (şufa)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu", "732, 733, 734"),
                    _m("5403", "Toprak Koruma ve Arazi Kullanımı Kanunu",
                       "8/İ (aile malları ortaklığında ortakların önalım hakkı)")],
        "sure": "TMK 733'teki üç ay / iki yıl hak düşürücü süreler — madde metninden teyit edin.",
        # 2026-09-20'de tr_mevzuat_icinde_ara ile doğrulandı. İlk taslak bu fıkrayı yürürlükte
        # sanıyordu; modülün neden metin değil adres taşıdığının canlı örneği.
        "uyari": "(1) 5403 m. 8/İ'deki SINIRDAŞ tarımsal arazi malikinin önalım hakkı fıkrası "
                 "28.10.2020 tarihli 7255 s. K. m. 20 ile KALDIRILDI. Satış tarihine göre eski "
                 "hükmün uygulanıp uygulanmayacağını içtihattan teyit edin. (2) TMK 733/1, "
                 "24.12.2025 tarihli 7571 s. K. m. 35 ile değişti: 2886 s. K. kapsamındaki "
                 "satışlarda ve CEBRÎ ARTIRMAYLA satışlarda (icra/e-Satış dâhil) önalım hakkı "
                 "kullanılamaz.",
        "parsel_verisi": "Paylı mülkiyette önalım pay satışına bağlıdır; parselin ifraz/tevhit "
                         "geçmişi (`gittigi_parseller`) hakkın konusunu değiştirir.",
        "ictihat": [_i('"önalım" +"hak düşürücü süre"'), _i('"5403" +"sınırdaş" +"önalım" +"7255"')],
    },
    "gecit_hakki": {
        "baslik": "Zorunlu geçit hakkı",
        "dayanak": [_m("4721", "Türk Medeni Kanunu", "747, 748"),
                    _m("6325", "Hukuk Uyuşmazlıklarında Arabuluculuk Kanunu",
                       "18/B (komşu hakkından kaynaklanan uyuşmazlıklarda dava şartı — kapsamı teyit edin)")],
        "parsel_verisi": "Genel yola cephe var mı, geçit hangi komşudan en az zararla geçer: komşu "
                         "parsellerle birlikte kroki ve kenar boyları gerekir.",
        "ictihat": [_i('"geçit hakkı" +"kesintisizlik"')],
    },
    "elatmanin_onlenmesi": {
        "baslik": "Elatmanın önlenmesi, yıkım (kal)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu", "683; haksız yapı 722-724; taşkın yapı 725")],
        "parsel_verisi": "Taşan kısmın alanı ve taşkın yapının sınıra göre konumu keşifle belirlenir; "
                         "kroki dava öncesi ön değerlendirmedir.",
        "ictihat": [_i('"elatmanın önlenmesi" +"taşkın yapı"')],
    },
    "tarim_arazisi": {
        "baslik": "Tarım arazisinde bölünme, devir ve miras",
        "dayanak": [_m("5403", "Toprak Koruma ve Arazi Kullanımı Kanunu",
                       "8 (asgari büyüklük), 8/A-8/K (devir ve miras), 13 (amaç dışı kullanım)")],
        "parsel_verisi": "Alan, asgari tarımsal arazi büyüklüğüyle karşılaştırılır.",
        "ictihat": [_i('"5403" +"asgari tarımsal arazi büyüklüğü"')],
    },
    "imar_uygulamasi": {
        "baslik": "İmar uygulaması (parselasyon), ifraz ve tevhit",
        "dayanak": [_m("3194", "İmar Kanunu", "15, 16 (ifraz-tevhit), 18 (arazi ve arsa düzenlemesi, DOP)")],
        "parsel_verisi": "Uygulama öncesi/sonrası alan farkı DOP kesintisi tartışmasının verisidir.",
        "ictihat": [{"arac": "tr_ictihat_ara",
                     "args": {"query": '"18. madde" +"düzenleme ortaklık payı"',
                              "courts": ["DANISTAYKARAR"]}}],
    },
    "enerji_guzergah": {
        "baslik": "Enerji tesisi / boru hattı güzergâhı: kamulaştırma ve irtifak",
        "dayanak": [_m("6446", "Elektrik Piyasası Kanunu", "19 (kamulaştırma)"),
                    _m("4646", "Doğal Gaz Piyasası Kanunu", "12 (kamulaştırma)"),
                    _m("2942", "Kamulaştırma Kanunu", "4 (irtifak), 27 (acele)")],
        "parsel_verisi": "Güzergâhın her parselden kestiği alan irtifak bedelinin temelidir. "
                         "Yüzlerce parsellik döküm Parsel Sorgu'dan elle toplanamaz: TKGM veri "
                         "paylaşım protokolü gerekir (bkz. `rehber`).",
        "kurum": [{"arac": "tr_kurum_karari_ara", "not": "EPDK kamu yararı / kamulaştırma kararları"}],
        "ictihat": [_i('"irtifak kamulaştırması" +"enerji nakil hattı"')],
    },
    "yabanci_edinim": {
        "baslik": "Yabancıların taşınmaz edinimi",
        "dayanak": [_m("2644", "Tapu Kanunu", "35, 36"),
                    _m("2565", "Askerî Yasak Bölgeler ve Güvenlik Bölgeleri Kanunu", "ilgili maddeler")],
        "ictihat": [_i('"2644" +"35. madde" +"yabancı"')],
    },
    "tapu_sicili_inceleme": {
        "baslik": "Tapu sicilini inceleme ve kayıt örneği alma",
        "dayanak": [_m("4721", "Türk Medeni Kanunu", "1020 (aleniyet: ilgisini inanılır kılma)"),
                    _m("6698", "Kişisel Verilerin Korunması Kanunu", "5, 8")],
        "parsel_verisi": "Parsel Sorgu malik bilgisi vermez. Malik, şerh, beyan ve rehin için kullanıcı "
                         "kendi e-Devlet/Web Tapu oturumundan belge alır (bkz. `baglanti`).",
        "ictihat": [],
    },
}

# Parsel niteliğindeki anahtar sözcük → özel rejim uyarısı.
NITELIK_REJIMLERI = [
    (("tarla", "bag", "baglik", "bahce", "cayir", "tarim", "sera", "findik"),
     "Tarım arazisi olabilir: 5403 s. K. asgari büyüklük, bölünme ve devir kısıtları.", "tarim_arazisi"),
    (("zeytin",), "Zeytinlik: 3573 s. K. kısıtları.", None),
    (("orman",), "Orman: 6831 s. K.; 2/B ise 6292 s. K.", None),
    (("mera", "yaylak", "kislak", "otlak"), "Mera: 4342 s. K. — özel mülkiyete konu olmaz, tahsis amacı değişikliği m. 14.", None),
    (("kiyi", "sahil", "kumsal", "deniz"), "Kıyı: 3621 s. K. ve Anayasa m. 43.", None),
    (("arsa",), "Arsa: imar durumu ilgili belediyeden alınır; 3194 s. K.", "imar_uygulamasi"),
    (("kat irtifak", "kat mulkiyet", "mesken", "dukkan", "apartman"), "Kat mülkiyeti rejimi: 634 s. K.", None),
    (("sit", "tescilli", "kultur varlig"), "Koruma alanı olabilir: 2863 s. K.", None),
]

_TR = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iisSgGuUoOcC")


# Hangi adresin `tr_mevzuat_icinde_ara` ile gerçekten açılıp bakıldığı. Listede olmayan
# adres ezberden yazılmıştır; "teyit" alanı boş dönen konu önce doğrulanmalıdır.
TEYIT: Dict[str, str] = {
    "ecrimisil": "2026-09-20: 2886 m. 75 (geriye doğru beş yıl sınırı metinde)",
    "ortakligin_giderilmesi": "2026-09-20: 6325 m. 18/B (b) bendi",
    "kamulastirma_bedel": "2026-09-20: 2942 m. 10, 12",
    "kamulastirmasiz_el_atma": "2026-09-20: 2942 geçici m. 6",
    "kadastro_itiraz": "2026-09-20: 3402 m. 12 (30 günlük ilan, kesinleşme)",
    "onalim": "2026-09-20: 5403 m. 8/İ — sınırdaş fıkrası mülga (7255/20); TMK 733/1 (7571/35 "
              "değişikliği), 735",
    "enerji_guzergah": "2026-09-20: 6446 m. 19, 4646 m. 12",
}


def konu_listesi() -> List[Dict[str, str]]:
    return [{"konu": k, "baslik": v["baslik"]} for k, v in KONULAR.items()]


def _eslesir(anahtar: str, katli: str, sozcukler: List[str]) -> bool:
    # Kısa anahtarlar alt dizgi olarak aranırsa "sit" → "site", "bag" → "bagimsiz"
    # eşleşir; bu yüzden tam sözcük aranır. Uzunlar ek almış hâli de yakalar ("bahcesi").
    if " " in anahtar:
        return anahtar in katli
    if len(anahtar) < 5:
        return any(s in (anahtar, anahtar + "i", anahtar + "si") for s in sozcukler)
    return any(s.startswith(anahtar) for s in sozcukler)


def nitelik_rejimleri(nitelik: str) -> List[Dict[str, Any]]:
    katli = (nitelik or "").translate(_TR).lower()
    parcalar = re.findall(r"[a-z0-9]+", katli)
    out = []
    for sozcukler, uyari, konu in NITELIK_REJIMLERI:
        if any(_eslesir(s, katli, parcalar) for s in sozcukler):
            kayit: Dict[str, Any] = {"uyari": uyari}
            if konu:
                kayit["konu"] = konu
            out.append(kayit)
    return out


def kopru(konu: str) -> Dict[str, Any]:
    kayit = dict(KONULAR[konu])
    kayit["mevzuat"] = [{"arac": "tr_mevzuat_ara", "args": {"number": d["mevzuat_no"]}}
                        for d in kayit["dayanak"]]
    kayit["teyit"] = TEYIT.get(konu, "YOK — adresler ezberden; alıntıdan önce doğrulayın")
    return kayit
