"""tkgm_hukuk — parselden hukuka köprü: hangi uyuşmazlıkta hangi dayanağa bakılır,
ve onu doğrulamak için hangi `tr_` aracı hangi argümanla çağrılır.

Bu modül hüküm METNİ taşımaz, yalnız adres taşır. Madde numaraları başlangıç
noktasıdır; mevzuat değişir, burada donmuş bir metin alıntılanırsa er geç
yürürlükten kalkmış bir hüküm "kanun böyle diyor" diye sunulur. Metin her
seferinde `tr_mevzuat_*` ile çekilir.

Değişiklik uyarıları da aynı sınırı taşır: `degisiklikler` yalnız teyit tarihinde
resmî metinde görülen değişiklikleri sayar. Teyitten SONRA yapılan bir değişikliği
yakalamak için her maddenin o gün taşıdığı değişiklik izleri (`IZLER`: "(Değişik …
7571/35 md.)", dipnottaki "7571 sayılı Kanunun 35 inci maddesiyle", AYM iptal
kararları) kayıtlıdır; `yeni_izler` çekilen güncel metinde kayıtta olmayan izi
döndürür. Liste eksik göründüğünde değil, tam göründüğünde yanıltır — SMK m. 120
olayının ve TMK 734/2'nin (7571/36) ilk sürümde atlanmasının dersi budur.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

DOGRULAMA = ("Dayanaklar ADRESTİR, metin değil. Alıntılamadan önce maddeyi çekin: `madde_cagrilari` "
             "içindeki tr_mevzuat_madde_getir çağrısı (tek çağrıda başlık + metin + atıf). Çekilen metindeki "
             "'(Değişik …)', '(Ek …)', '(Mülga …)', '(İptal …)' notlarını `degisiklikler` ve `izler` ile "
             "karşılaştırın: teyit tarihinden sonraki değişiklik bu listede YOKTUR. Süreler ve içtihat için "
             "önerilen tr_ictihat_ara sorgularını çalıştırın; belirtilen süreyi teyitsiz yazmayın.")

TEYIT_TARIHI = "2026-09-22"


def _m(no: str, ad: str, maddeler: str, madde_no: List[str]) -> Dict[str, Any]:
    """Bir dayanak adresi. `maddeler` insan için etiket, `madde_no` tr_mevzuat_madde_getir için liste."""
    return {"mevzuat_no": no, "ad": ad, "maddeler": maddeler, "madde_no": list(madde_no)}


def _i(sorgu: str) -> Dict[str, Any]:
    return {"arac": "tr_ictihat_ara", "args": {"query": sorgu}}


def _d(madde: str, degistiren: str, kabul: str, yururluk: str, ozet: str) -> Dict[str, str]:
    return {"madde": madde, "degistiren": degistiren, "kabul": kabul, "yururluk": yururluk, "ozet": ozet}


# Her madde numarası, etiketi ve değişiklik notu 2026-09-22'de resmî metinle (Bedesten,
# mevzuat.gov.tr külliyatı) madde madde karşılaştırıldı; bkz. TEYIT ve IZLER.
KONULAR: Dict[str, Dict[str, Any]] = {
    "tapu_iptal_tescil": {
        "baslik": "Tapu iptali ve tescil (yolsuz tescil)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu",
                       "1023 (iyiniyetli üçüncü kişinin korunması), 1024 (yolsuz tescil; iyiniyetli olmayan "
                       "üçüncü kişi), 1025 (yolsuz tescilde düzeltme davası); Devletin sorumluluğu 1007",
                       ["1007", "1023", "1024", "1025"]),
                    _m("6100", "HMK", "12 (taşınmazın aynından doğan davalarda kesin yetki)", ["12"])],
        "parsel_verisi": "Ada/parsel ve taşınmazın bulunduğu yer yetkili mahkemeyi belirler; "
                         "`gittigi_parseller` doluysa parsel ifraz/tevhit görmüştür, dava güncel parsele yöneltilir.",
        "ictihat": [_i('"tapu iptali ve tescil" +"yolsuz tescil"'),
                    _i('"TMK 1023" +"iyiniyet"')],
    },
    "ecrimisil": {
        "baslik": "Ecrimisil (haksız işgal tazminatı)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu", "995 (iyiniyetli olmayan zilyedin tazmin yükümlülüğü)",
                       ["995"]),
                    _m("2886", "Devlet İhale Kanunu",
                       "75 (Hazine, özel bütçeli idare ve mazbut vakıf taşınmazları: tespit tarihinden geriye "
                       "en çok beş yıl)", ["75"])],
        "sure": "Özel kişiler arasında 5 yıllık zamanaşımı YİBK 25.05.1938, 29/10'a dayandırılır — sorguyla "
                "teyit edin. 2886 m. 75'teki beş yıl, idarenin ecrimisilinde geriye dönük tespit sınırıdır.",
        "parsel_verisi": "İşgal edilen kısmın alanı krokiden/keşiften gelir; paydaşlar arasında "
                         "intifadan men koşulu aranır.",
        "ictihat": [_i('"ecrimisil" +"zamanaşımı" +"beş yıl"'),
                    _i('"ecrimisil" +"intifadan men"')],
    },
    "ortakligin_giderilmesi": {
        "baslik": "Ortaklığın giderilmesi (izale-i şüyu)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu",
                       "698 (paylaşma istemi), 699 (paylaşma biçimi: aynen bölme ya da satış)", ["698", "699"]),
                    _m("6100", "HMK", "4/1-b (sulh hukuk mahkemesinin görevi)", ["4"]),
                    _m("6325", "Hukuk Uyuşmazlıklarında Arabuluculuk Kanunu",
                       "18/B (1-b: dava şartı arabuluculuk; 3: taşınmazla ilgili anlaşma belgesinin icra "
                       "edilebilirlik şerhi taşınmazın bulunduğu yer sulh hukuk mahkemesinden; 5: şerhten sonra "
                       "resmî senet düzenlenmeksizin tescil — 7531/26)", ["18/B"])],
        "parsel_verisi": "Aynen taksim mümkün mü sorusu alan, cephe ve imar/5403 asgari "
                         "büyüklüklerine bağlıdır; `geometri` çıktısı bilirkişi öncesi ön fikir verir.",
        "ictihat": [_i('"ortaklığın giderilmesi" +"aynen taksim"')],
    },
    "kamulastirma_bedel": {
        "baslik": "Kamulaştırma bedelinin tespiti ve tescil",
        "dayanak": [_m("2942", "Kamulaştırma Kanunu",
                       "4 (irtifak), 7 (idari şerh: altı ay içinde m. 10 davası belgesi verilmezse şerh re'sen "
                       "silinir), 8 (satın alma usulü), 10 (bedel tespiti ve tescil), 11 (değer esasları), "
                       "12 (kısmen kamulaştırma), 27 (acele kamulaştırma)",
                       ["4", "7", "8", "10", "11", "12", "27"])],
        "uyari": "2942 m. 10 AYM kararlarıyla birçok kez kısmen iptal edildi (16.07.2020 K.2020/39; 05.04.2023 "
                 "K.2023/69; 25.12.2024 K.2024/232; 10.07.2025 K.2025/141). 12.02.2026 tarihli E.2025/190, "
                 "K.2026/38 kararıyla 'Tescil hükmü kesin olup' ibaresi iptal edildi; iptal 21.02.2027'de "
                 "yürürlüğe girer. Yürürlükteki metni çekin, iptallerin yürürlük tarihini dava tarihine göre "
                 "değerlendirin.",
        "degisiklikler": [_d("2942 m. 10", "AYM E.2025/190, K.2026/38 (iptal)", "12.02.2026", "21.02.2027",
                             "'Tescil hükmü kesin olup' ibaresi iptal")],
        "parsel_verisi": "Kısmi kamulaştırmada kalan kısmın alanı ve biçimi değer kaybı "
                         "tartışmasının temelidir.",
        "ictihat": [_i('"kamulaştırma bedeli" +"emsal"'),
                    _i('"kısmen kamulaştırma" +"değer azalışı"')],
    },
    "kamulastirmasiz_el_atma": {
        "baslik": "Kamulaştırmasız el atma (fiilî / hukuki)",
        "dayanak": [_m("2942", "Kamulaştırma Kanunu",
                       "geçici 6 (9.10.1956-4.11.1983 arasında fiilen el konulan taşınmazlar: önce uzlaşma dava "
                       "şartı, uzlaşmazlık tutanağından itibaren üç ay içinde bedel tespiti davası), ek 1 (uygulama "
                       "imar planıyla hukuken kısıtlanan taşınmazlar — AYM 2018 ve 2025 iptallerini kontrol edin), "
                       "ek 4 (hükmedilen bedelin ödenmesi; harç fıkrası AYM iptali — bkz. uyarı)",
                       ["geçici 6", "ek 1", "ek 4"])],
        "uyari": "2942 ek m. 4'ün son fıkrası (bedel ve tazminat kararlarında mahkeme ve icra harçlarının maktu "
                 "belirlenmesi) AYM'nin 13.05.2026 tarihli, E.2026/24, K.2026/106 sayılı kararıyla "
                 "'kamulaştırmasız el atma nedeniyle açılan tazminat davaları' yönünden iptal edildi (değişiklik "
                 "cetvelindeki yürürlük: 07.08.2026). Harç ve masraf hesabını dava tarihine göre teyit edin.",
        "degisiklikler": [_d("2942 ek 4 (son fıkra)", "AYM E.2026/24, K.2026/106 (iptal)", "13.05.2026",
                             "07.08.2026", "kamulaştırmasız el atma tazminat davaları yönünden maktu harç "
                             "kuralı iptal"),
                          _d("2942 ek 1 (üçüncü cümle)", "AYM E.2024/135, K.2025/20 (iptal)", "16.01.2025",
                             "30.01.2026", "iptal edilen cümle; yürürlükteki metni çekin")],
        "parsel_verisi": "El atılan kısmın alanı ve imar planındaki kullanım kararı görev "
                         "(adli/idari yargı) ayrımını belirler.",
        "ictihat": [_i('"kamulaştırmasız el atma" +"hukuki el atma"')],
    },
    "kadastro_itiraz": {
        "baslik": "Kadastro tespitine itiraz / kadastro öncesi nedene dayalı dava",
        "dayanak": [_m("3402", "Kadastro Kanunu",
                       "11 (askı ilanı: 30 gün), 12 (kesinleşme; 12/3 on yıllık hak düşürücü süre), "
                       "22 (evvelce kadastrosu yapılan yerler; 22/a yenileme)", ["11", "12", "22"])],
        "sure": "m. 11: askı ilanı 30 gün; itiraz eden ilan süresi içinde kadastro mahkemesinde dava açar. "
                "m. 12/3: tutanağın kesinleştiği tarihten itibaren on yıl geçtikten sonra kadastrodan önceki "
                "hukuki sebeplere dayanarak itiraz ve dava yok (hak düşürücü). Madde metninden teyit edin.",
        "ictihat": [_i('"3402" +"12/3" +"hak düşürücü"')],
    },
    "alan_farki": {
        "baslik": "Hesap alanı ile tapu alanı farkı / sınır uyuşmazlığı",
        "dayanak": [_m("3402", "Kadastro Kanunu",
                       "41 (hatalar ve düzeltme işlemleri — 7579 s. K. m. 16 ile 2026'da değişik: fark, yanılma "
                       "sınırı (tecviz) ve hata tanımları)", ["41"]),
                    _m("4721", "Türk Medeni Kanunu", "719 (sınırların belirlenmesi: plan ile zemin tutmazsa "
                       "plandaki sınır asıldır)", ["719"])],
        "sure": "3402 m. 41/1: düzeltme malike ve diğer hak sahiplerine tebliğ edilir; tebliğden itibaren otuz gün "
                "içinde sulh hukuk mahkemesinde dava açılmazsa düzeltme kesinleşir. m. 41 uygulamasında m. 12'deki "
                "hak düşürücü süre aranmaz. Madde metninden teyit edin.",
        "uyari": "3402 m. 41, 07.05.2026 tarihli 7579 s. K. m. 16 ile değişti (yürürlük 22.05.2026): 'fark', "
                 "'yanılma sınırı (tecviz)' ve 'hata' (farkın yanılma sınırını aşması) tanımlandı; yanılma sınırı "
                 "dışındaki farklar ilgilinin müracaatı veya re'sen düzeltilir, içindekiler aynı usulle "
                 "düzeltilebilir ve farkın tamamı düzeltmeye konu edilir. Bu tarihten önceki içtihat eski metne "
                 "dayanır. Bu aracın fark_m2/fark_yuzde değeri m. 41 anlamında 'fark' DEĞİLDİR (o, tapu planı "
                 "değerleri ile güncel teknolojiyle yeniden hesaplanan değerler arasındaki farktır).",
        "degisiklikler": [_d("3402 m. 41", "7579 s. K. m. 16", "07.05.2026", "22.05.2026",
                             "başlık 'Hatalar ve düzeltme işlemleri'; birinci ve son fıkra değişik; fark, "
                             "yanılma sınırı ve hata tanımları eklendi")],
        "parsel_verisi": "`geometri` aracındaki fark_m2 / fark_yuzde yalnız İŞARETTİR: Parsel Sorgu "
                         "verisi bilgi amaçlıdır, düzeltme talebi kadastro müdürlüğü kayıtlarına dayanır.",
        "ictihat": [_i('"3402" +"41. madde" +"yüzölçümü"')],
    },
    "onalim": {
        "baslik": "Yasal önalım (şufa)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu",
                       "732, 733 (7571/35 ile değişik), 734 (7571/36 ile değişik), 735 (sözleşmeden doğan "
                       "önalım), geçici 1 (7571/37: geçiş hükmü)",
                       ["732", "733", "734", "735", "geçici 1"]),
                    _m("5403", "Toprak Koruma ve Arazi Kullanımı Kanunu",
                       "8/İ (aile malları ortaklığında ortakların önalım hakkı; kullanılmasında TMK uygulanır)",
                       ["8/İ"])],
        "sure": "TMK 733 son fıkra (7571/35 ile değişik): satışın hak sahibine bildirildiği tarihten üç ay ve her "
                "hâlde satıştan itibaren BİR YIL (hak düşürücü). 7571 öncesi metinde 'iki yıl' idi; 25.12.2025'ten "
                "önce yapılmış satışlarda geçici m. 1/1 uyarınca değişiklikten önceki hükümler uygulanır. "
                "Madde metninden teyit edin.",
        # 2026-09-20'deki ilk teyit yalnız 733/1'i görmüştü; 734/2 (rayiç bedel), 733 son fıkra
        # (iki yıl → bir yıl) ve geçici m. 1 atlanmıştı. Tam görünen eksik liste, eksik görünen
        # listeden daha tehlikelidir: kullanıcı eski kuralla dava açar.
        "uyari": "(1) 24.12.2025 tarihli 7571 s. K. (yürürlük 25.12.2025) TMK önalım hükümlerini birlikte "
                 "değiştirdi: (a) 733/1 (m. 35): 2886 s. Devlet İhale Kanunu kapsamında yapılan satışlar ile "
                 "cebrî artırmayla satışlarda önalım hakkı kullanılamaz. (b) 733 son fıkra (m. 35): satıştan "
                 "itibaren azami süre 'iki yıl' iken 'bir yıl' oldu (bildirimden itibaren üç ay aynı). (c) 734/2 "
                 "(m. 36): dava konusu payın RAYİÇ BEDELİ hâkim tarafından gecikmeksizin belirlenir; önalım hakkı "
                 "sahibi bu rayiç bedel ile alıcıya düşen tapu giderlerini hâkimin belirlediği yere verilen KESİN "
                 "SÜRE içinde nakden yatırır; yatırmazsa payın tesciline karar verilemez; bedel hüküm "
                 "kesinleşince nemasıyla ilgilisine ödenir. Tapudaki satış bedeli değil, rayiç bedel esastır. "
                 "(2) Geçiş (TMK geçici m. 1, 7571/37): 733'teki değişiklikler 25.12.2025'ten ÖNCE yapılmış "
                 "satışlara uygulanmaz, o satışlarda eski hükümler sürer; 734'teki değişiklikler o tarihten önce "
                 "AÇILMIŞ davalara da uygulanır. Satış tarihini ve dava tarihini ayrı ayrı kontrol edin. "
                 "(3) 5403 m. 8/İ'nin ikinci fıkrası 28.10.2020 tarihli 7255 s. K. m. 20 ile KALDIRILDI (sınırdaş "
                 "tarımsal arazi maliklerinin önalım hakkı; mülga metin yürürlükteki külliyatta görünmez). Satış "
                 "tarihine göre eski hükmün uygulanıp uygulanmayacağını içtihattan teyit edin. 8/İ'deki aile "
                 "malları ortaklığı önalımında TMK hükümleri, dolayısıyla 7571 değişiklikleri de uygulanır.",
        "degisiklikler": [
            _d("TMK 733/1", "7571 s. K. m. 35", "24.12.2025", "25.12.2025",
               "2886 s. K. kapsamındaki satışlar ve cebrî artırmayla satışlarda önalım kullanılamaz"),
            _d("TMK 733 son fıkra", "7571 s. K. m. 35", "24.12.2025", "25.12.2025",
               "satıştan itibaren azami süre 'iki yıl' → 'bir yıl'"),
            _d("TMK 734/2", "7571 s. K. m. 36", "24.12.2025", "25.12.2025",
               "rayiç bedel hâkimce belirlenir; rayiç bedel + alıcıya düşen tapu giderleri kesin sürede "
               "nakden yatırılır, yatırılmazsa tescile karar verilemez"),
            _d("TMK geçici 1", "7571 s. K. m. 37 (ek)", "24.12.2025", "25.12.2025",
               "733 değişiklikleri yürürlükten önceki satışlara uygulanmaz; 734 değişiklikleri önceden "
               "açılmış davalara da uygulanır"),
            _d("5403 m. 8/İ ikinci fıkra", "7255 s. K. m. 20 (mülga)", "28.10.2020", "28.10.2020",
               "sınırdaş tarımsal arazi maliklerinin önalım hakkı kaldırıldı"),
        ],
        "parsel_verisi": "Paylı mülkiyette önalım pay satışına bağlıdır; parselin ifraz/tevhit "
                         "geçmişi (`gittigi_parseller`) hakkın konusunu değiştirir.",
        "ictihat": [_i('"önalım" +"hak düşürücü süre"'), _i('"önalım" +"rayiç bedel" +"7571"'),
                    _i('"5403" +"sınırdaş" +"önalım" +"7255"')],
    },
    "gecit_hakki": {
        "baslik": "Zorunlu geçit hakkı",
        "dayanak": [_m("4721", "Türk Medeni Kanunu",
                       "747 (zorunlu geçit: tam bedel karşılığında), 748 (diğer geçit hakları)", ["747", "748"]),
                    _m("6325", "Hukuk Uyuşmazlıklarında Arabuluculuk Kanunu",
                       "18/B (1-ç: komşu hakkından kaynaklanan uyuşmazlıklarda dava şartı arabuluculuk; geçit "
                       "hakları TMK'da 'Komşu hakkı' başlığı altındadır — kapsamı içtihatla teyit edin)", ["18/B"])],
        "parsel_verisi": "Genel yola cephe var mı, geçit hangi komşudan en az zararla geçer: komşu "
                         "parsellerle birlikte kroki ve kenar boyları gerekir.",
        "ictihat": [_i('"geçit hakkı" +"kesintisizlik"')],
    },
    "elatmanin_onlenmesi": {
        "baslik": "Elatmanın önlenmesi, yıkım (kal)",
        "dayanak": [_m("4721", "Türk Medeni Kanunu",
                       "683 (mülkiyetin içeriği; elatmanın önlenmesi davası); arazideki yapılar 722-724; "
                       "taşkın yapı 725", ["683", "722", "723", "724", "725"])],
        "parsel_verisi": "Taşan kısmın alanı ve taşkın yapının sınıra göre konumu keşifle belirlenir; "
                         "kroki dava öncesi ön değerlendirmedir.",
        "ictihat": [_i('"elatmanın önlenmesi" +"taşkın yapı"')],
    },
    "tarim_arazisi": {
        "baslik": "Tarım arazisinde bölünme, devir ve miras",
        "dayanak": [_m("5403", "Toprak Koruma ve Arazi Kullanımı Kanunu",
                       "8 (sınıflandırma, asgari tarımsal arazi büyüklüğü — altında ifraz ve hisselendirme "
                       "yasağı; 7584/23 ile 2026'da kooperatif edinimine ilişkin fıkra eklendi), 8/A (yeter "
                       "gelirli büyüklük), 8/B-8/J (miras ve devir; 8/İ önalım), 8/K (arazi edindirme), "
                       "13 (amaç dışı kullanım)",
                       ["8", "8/A", "8/B", "8/İ", "8/K", "13"])],
        "parsel_verisi": "Alan, asgari tarımsal arazi büyüklüğüyle karşılaştırılır.",
        "ictihat": [_i('"5403" +"asgari tarımsal arazi büyüklüğü"')],
    },
    "imar_uygulamasi": {
        "baslik": "İmar uygulaması (parselasyon), ifraz ve tevhit",
        "dayanak": [_m("3194", "İmar Kanunu",
                       "15 (ifraz ve tevhit), 16 (tescil ve şüyuun izalesi: ifraz/tevhit onayı), 18 "
                       "(parselasyon planı, düzenleme ortaklık payı — DOP'un kullanılacağı yerleri sayan cümle "
                       "7534/6 ile 2024'te değişti)", ["15", "16", "18"])],
        "parsel_verisi": "Uygulama öncesi/sonrası alan farkı DOP kesintisi tartışmasının verisidir.",
        "ictihat": [{"arac": "tr_ictihat_ara",
                     "args": {"query": '"18. madde" +"düzenleme ortaklık payı"',
                              "courts": ["DANISTAYKARAR"]}}],
    },
    "enerji_guzergah": {
        "baslik": "Enerji tesisi / boru hattı güzergâhı: kamulaştırma ve irtifak",
        "dayanak": [_m("6446", "Elektrik Piyasası Kanunu",
                       "19 (taşınmaz temini — 7257/38 ile başlığıyla değişik: 2942'ye göre Kurum eliyle, Kurul "
                       "kararı kamu yararı kararı yerine geçer; 19/5 irtifak alanı 7501/12)", ["19"]),
                    _m("4646", "Doğal Gaz Piyasası Kanunu", "12/a (kamulaştırma: Kurul lüzum kararı kamu yararı "
                       "kararı yerine geçer)", ["12"]),
                    _m("2942", "Kamulaştırma Kanunu", "4 (irtifak), 27 (acele kamulaştırma)", ["4", "27"])],
        "parsel_verisi": "Güzergâhın her parselden kestiği alan irtifak bedelinin temelidir. "
                         "Yüzlerce parsellik döküm Parsel Sorgu'dan elle toplanamaz: TKGM veri "
                         "paylaşım protokolü gerekir (bkz. `baslangic`).",
        "kurum": [{"arac": "tr_kurum_karari_ara", "not": "EPDK kamu yararı / kamulaştırma kararları"}],
        "ictihat": [_i('"irtifak kamulaştırması" +"enerji nakil hattı"')],
    },
    "yabanci_edinim": {
        "baslik": "Yabancıların taşınmaz edinimi",
        "dayanak": [_m("2644", "Tapu Kanunu",
                       "35 (yabancı gerçek kişiler ve yabancı ticaret şirketleri: ilçenin özel mülkiyete konu "
                       "yüzölçümünün %10'u ve kişi başına 30 hektar sınırı), 36 (yabancı sermayeli Türk "
                       "şirketleri)", ["35", "36"]),
                    _m("2565", "Askerî Yasak Bölgeler ve Güvenlik Bölgeleri Kanunu",
                       "9/b (ikinci derece kara askerî yasak bölgesinde yabancılar taşınmaz edinemez), 28 "
                       "(stratejik bölgelerde Cumhurbaşkanı kararıyla edinim yasağı), 29 (tasfiye)",
                       ["9", "28", "29"])],
        "ictihat": [_i('"2644" +"35. madde" +"yabancı"')],
    },
    "tapu_sicili_inceleme": {
        "baslik": "Tapu sicilini inceleme ve kayıt örneği alma",
        "dayanak": [_m("4721", "Türk Medeni Kanunu", "1020 (aleniyet: ilgisini inanılır kılma)", ["1020"]),
                    _m("6698", "Kişisel Verilerin Korunması Kanunu", "5 (işleme şartları), 8 (aktarma)",
                       ["5", "8"])],
        "parsel_verisi": "Parsel Sorgu malik bilgisi vermez. Malik, şerh, beyan ve rehin için kullanıcı "
                         "kendi e-Devlet/Web Tapu oturumundan belge alır (bkz. `baglanti`).",
        "ictihat": [],
    },
}

# Parsel niteliğindeki anahtar sözcük → özel rejim uyarısı. Kanun numaraları ve 4342 m. 14,
# AY m. 43 2026-09-22'de resmî metinle teyit edildi (6292, 3573, 6831, 3621, 634, 2863 adları
# yürürlükteki metinlerde bu numaralarla geçer).
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


# Her konunun adresleri hangi gün, neyle karşılaştırıldı. Bir konu buraya ancak bütün
# dayanak maddeleri resmî metinde açılıp etiketiyle eşleştirildikten sonra girer.
_TEYIT_ORTAK = "resmî metinle (Bedesten / mevzuat.gov.tr) madde madde karşılaştırıldı"
TEYIT: Dict[str, str] = {
    "tapu_iptal_tescil": "%s: %s — TMK 1007, 1023-1025; HMK 12" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "ecrimisil": "%s: %s — TMK 995; 2886 m. 75 (geriye doğru beş yıl sınırı metinde)" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "ortakligin_giderilmesi": "%s: %s — TMK 698, 699; HMK 4/1-b; 6325 m. 18/B (1-b, 3, 5)" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "kamulastirma_bedel": "%s: %s — 2942 m. 4, 7, 8, 10, 11, 12, 27" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "kamulastirmasiz_el_atma": "%s: %s — 2942 geçici m. 6, ek m. 1 ve ek m. 4 (AYM 13.05.2026 iptali dipnotta)"
                               % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "kadastro_itiraz": "%s: %s — 3402 m. 11 (30 gün), 12/3 (on yıl), 22" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "alan_farki": "%s: %s — 3402 m. 41 (7579/16, yürürlük 22.05.2026); TMK 719" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "onalim": "%s: %s — TMK 732, 733 (7571/35: birinci fıkra ve 'iki yıl' → 'bir yıl'), 734 (7571/36), 735, "
              "geçici 1 (7571/37); 5403 m. 8/İ (ikinci fıkra mülga, 7255/20)" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "gecit_hakki": "%s: %s — TMK 747, 748; 6325 m. 18/B-1/ç" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "elatmanin_onlenmesi": "%s: %s — TMK 683, 722-725" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "tarim_arazisi": "%s: %s — 5403 m. 8, 8/A-8/K, 13" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "imar_uygulamasi": "%s: %s — 3194 m. 15, 16, 18" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "enerji_guzergah": "%s: %s — 6446 m. 19; 4646 m. 12/a; 2942 m. 4, 27" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "yabanci_edinim": "%s: %s — 2644 m. 35, 36; 2565 m. 9/b, 28, 29" % (TEYIT_TARIHI, _TEYIT_ORTAK),
    "tapu_sicili_inceleme": "%s: %s — TMK 1020; 6698 m. 5, 8" % (TEYIT_TARIHI, _TEYIT_ORTAK),
}
TEYIT_YOK = "YOK — adresler ezberden; alıntıdan önce doğrulayın"


# Teyit günü her maddenin resmî metninde görülen değişiklik izleri: satır içi notlar
# ("(Değişik … 24/12/2025-7571/35 md.)"), maddeye bağlı dipnotlar ("7571 sayılı Kanunun
# 35 inci maddesiyle") ve AYM iptal kararları ("AYM K.2018/111"). `yeni_izler` güncel metinde
# bu kümede olmayan izi döndürür: teyitten sonra yapılmış bir değişikliğin işaretidir.
# Maddenin kenar başlığındaki dipnotlar da maddeye sayıldı (3402 m. 41'in başlığı 5304/9 ile
# değişti); bu yüzden bir önceki maddenin son dipnotu kümeye karışabilir (TMK 734'te 7571/35).
# Fazla iz kaçırılan değişiklik değil, yalnız fazladan sessizliktir.
IZLER: Dict[str, Dict[str, List[str]]] = {
    "4721": {"1007": [], "1023": [], "1024": [], "1025": [], "995": [], "698": [], "699": [], "719": [],
             "732": [], "733": ["7571/35"], "734": ["7571/35", "7571/36"], "735": [], "geçici 1": ["7571/37"],
             "747": [], "748": [], "683": [], "722": [], "723": [], "724": [], "725": [], "1020": []},
    "6100": {"12": [], "4": []},
    "2886": {"75": ["5737/79", "6009/24", "7103/26"]},
    "6325": {"18/B": ["7445/37", "7531/26"]},
    "2942": {"4": ["6552/99", "6639/28", "AYM K.2015/49"], "7": ["4650/2"], "8": ["4650/3", "6745/31"],
             "10": ["4650/4", "4650/5", "4650/6", "6459/6", "6754/38", "7139/26", "7418/28", "AYM K.2020/39",
                    "AYM K.2023/69", "AYM K.2024/232", "AYM K.2025/141", "AYM K.2026/38"],
             "11": ["4650/6", "6754/38", "7139/27", "AYM K.2003/29", "AYM K.2016/45", "AYM K.2019/22"],
             "12": ["4650/4", "7103/27"], "27": ["4650/15", "7139/29"],
             "geçici 6": ["5999/1", "6487/21", "6745/34", "6754/40", "AYM K.2014/176"],
             "ek 1": ["6745/33", "7421/3", "AYM K.2018/111", "AYM K.2025/20"],
             "ek 4": ["7421/5", "AYM K.2026/106"]},
    "3402": {"11": [], "12": ["5841/2", "AYM K.2011/77"], "22": ["5304/6"], "41": ["5304/9", "7579/16"]},
    "5403": {"8/İ": ["6537/5", "7255/20"], "8": ["5578/2", "6537/4", "7139/40", "7584/23"],
             "8/A": ["6537/5", "7255/17", "7255/18"], "8/B": ["6537/5", "7255/18"],
             "8/K": ["6537/5", "7442/37"], "13": ["5578/3", "5751/1", "6537/6", "7255/21", "AYM K.2023/68"]},
    "3194": {"15": ["7181/8"], "16": [], "18": ["5006/1", "7181/9", "7221/7", "7333/10", "7534/6"]},
    "6446": {"19": ["7257/38", "7501/12"]},
    "4646": {"12": ["5784/19"]},
    "2644": {"35": ["5444/1", "6302/1"], "36": ["5782/2", "6302/2"]},
    "2565": {"9": ["5082/2"], "28": [], "29": []},
    "6698": {"5": [], "8": []},
}


def _madde_anahtari(madde: str) -> str:
    # Türkçe küçültme: 5403'te 8/I ile 8/İ AYRI maddelerdir; "I".lower() == "i" onları birleştirirdi.
    s = re.sub(r"\s+", " ", str(madde).strip()).replace("I", "ı").replace("İ", "i").lower()
    return re.sub(r"^gecici\b", "geçici", s)


_IZ_SATIR_ICI = re.compile(r"\d{1,2}/\d{1,2}/\d{4}\s*[-–]\s*(\d{3,4})\s*/\s*(\d{1,3})\s*md", re.I)
_IZ_DIPNOT = re.compile(r"(\d{3,4})\s+sayılı\s+Kanun\S*\s+(\d{1,3})\s*\S*\s+maddesi(?:yle|\s+ile)\b", re.I)
# Künye iki yazımla geçer: "E.: 2024/135; K.: 2025/20" ve "Esas No.:2018/104; Karar No.: 2020/39".
_IZ_AYM = re.compile(r"Anayasa\s+Mahkemesi\S*\s+\d{1,2}/\d{1,2}/\d{4}\s+tarihli\s+ve\s+(?:E\.?|Esas\s+No\.?)\s*:?\s*"
                     r"(\d{4})\s*/\s*(\d+)\s*[,;]?\s*(?:K\.?|Karar\s+No\.?)\s*:?\s*(\d{4})\s*/\s*(\d+)", re.I)


def degisiklik_izleri(metin: str) -> List[str]:
    """Madde metnindeki değişiklik izleri: '7571/35' (kanun/madde) ve 'AYM K.2026/106'.

    tr_mevzuat_madde_getir çıktısına da, mevzuat.gov.tr metnine de uygulanır; dipnotlar
    varsa onlar da okunur. KHK ile yapılan ibare değişiklikleri (700 s. KHK) sayılmaz.
    """
    duz = re.sub(r"\s+", " ", metin or "")
    izler = {"%s/%s" % (m.group(1), m.group(2)) for m in _IZ_SATIR_ICI.finditer(duz)}
    izler |= {"%s/%s" % (m.group(1), m.group(2)) for m in _IZ_DIPNOT.finditer(duz)}
    izler |= {"AYM K.%s/%s" % (m.group(3), m.group(4)) for m in _IZ_AYM.finditer(duz)}
    return sorted(izler)


def kayitli_izler(mevzuat_no: str, madde_no: str) -> Optional[List[str]]:
    """Teyit günü kaydedilen izler; madde teyit edilmemişse None."""
    kanun = IZLER.get(str(mevzuat_no))
    if kanun is None:
        return None
    for k, v in kanun.items():
        if _madde_anahtari(k) == _madde_anahtari(madde_no):
            return list(v)
    return None


def yeni_izler(mevzuat_no: str, madde_no: str, metin: str) -> List[str]:
    """Güncel metinde olup teyit kaydında olmayan değişiklik izleri.

    Boş liste: teyitten beri metne yeni not düşmemiş. Dolu liste: köprüdeki uyarı ve
    etiketler o değişikliği bilmiyor; madde yeniden okunmadan köprüye güvenilmez.
    Teyit edilmemiş maddede bütün izler "yeni" sayılır.
    """
    kayit = set(kayitli_izler(mevzuat_no, madde_no) or [])
    return [iz for iz in degisiklik_izleri(metin) if iz not in kayit]


def denetle(konu: str, getir: Callable[[str, str], Optional[str]]) -> Dict[str, Any]:
    """Bir konunun bütün dayanak maddelerini `getir(mevzuat_no, madde_no)` ile çekip
    teyit kaydıyla karşılaştırır. Çevrimdışı testte fikstür, canlı denetimde
    tr_mevzuat_madde_getir sarmalayıcısı verilir.

    Dönen: {"yeni_izler": {"4721 m. 734": [...]}, "cekilemeyen": [...], "teyitsiz": [...]}
    Hepsi boşsa köprü, çekilen metinle çelişen bir değişiklik bilgisi taşımıyor demektir.
    """
    sonuc: Dict[str, Any] = {"yeni_izler": {}, "cekilemeyen": [], "teyitsiz": []}
    for d in KONULAR[konu]["dayanak"]:
        for madde in d["madde_no"]:
            ad = "%s m. %s" % (d["mevzuat_no"], madde)
            if kayitli_izler(d["mevzuat_no"], madde) is None:
                sonuc["teyitsiz"].append(ad)
            metin = getir(d["mevzuat_no"], madde)
            if not metin:
                sonuc["cekilemeyen"].append(ad)
                continue
            yeni = yeni_izler(d["mevzuat_no"], madde, metin)
            if yeni:
                sonuc["yeni_izler"][ad] = yeni
    return sonuc


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


def madde_cagrilari(konu: str) -> List[Dict[str, Any]]:
    """Her dayanak için hazır tr_mevzuat_madde_getir çağrısı (tek çağrıda en çok 10 madde)."""
    out = []
    for d in KONULAR[konu]["dayanak"]:
        liste = d["madde_no"]
        for i in range(0, len(liste), 10):
            out.append({"arac": "tr_mevzuat_madde_getir",
                        "args": {"number": d["mevzuat_no"], "madde_no": liste[i:i + 10]}})
    return out


def _konu_izleri(konu: str) -> Dict[str, Dict[str, List[str]]]:
    out: Dict[str, Dict[str, List[str]]] = {}
    for d in KONULAR[konu]["dayanak"]:
        for madde in d["madde_no"]:
            izler = kayitli_izler(d["mevzuat_no"], madde)
            if izler:
                out.setdefault(d["mevzuat_no"], {})[madde] = izler
    return out


def kopru(konu: str) -> Dict[str, Any]:
    kayit = dict(KONULAR[konu])
    kayit["mevzuat"] = [{"arac": "tr_mevzuat_ara", "args": {"number": d["mevzuat_no"]}}
                        for d in kayit["dayanak"]]
    kayit["madde_cagrilari"] = madde_cagrilari(konu)
    kayit["teyit"] = TEYIT.get(konu, TEYIT_YOK)
    izler = _konu_izleri(konu)
    if izler:
        kayit["izler"] = izler
    return kayit
