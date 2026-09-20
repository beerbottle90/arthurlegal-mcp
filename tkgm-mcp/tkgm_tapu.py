"""tkgm_tapu — kullanıcının KENDİ e-Devlet / Web Tapu oturumundan aldığı tapu kaydı
metnini yapılandırır. Bu sunucu hiçbir oturuma girmez, belgeyi kendisi çekmez.

Girdi metindir: PDF'i model kendisi okuyup metnini verir ya da kullanıcı yapıştırır.
Burada PDF çözümleyici yazılmadı; stdlib'le yazılan bir PDF metin çıkarıcı Türkçe
harfleri (ToUnicode CMap) sessizce bozar ve bozuk bir malik adı, okunamayan bir
belgeden daha tehlikelidir.

Katkısı şema, maskeleme, çapraz kontrol ve işarettir:
- MASKELEME iki katmandır. (1) Belirlenimci kalıplar: TCKN (NVİ sağlaması doğrulanır,
  ayraçlı yazım dâhil), IBAN (mod-97), etiketli VKN, telefon, e-posta. (2) Malik ADLARI:
  ayrıştırıcı adın hangi dizge olduğunu `Malik:` satırından ZATEN bilir, bu yüzden ad
  tespiti için NLP gerekmez — ad {{MALİK-nn}} olur ve kayıdın HER yerinde, şerh ve rehin
  satırları dâhil, aynı etiketi alır. Eşleştirme döndürülmez, diske yazılmaz.
  Maskelenmeyenler çıktının `kvkk` alanında ADIYLA sayılır: şerhteki üçüncü kişi ve şirket
  adları, adres, doğum tarihi. Tam takma adlandırma Arthur Mask'in işidir; bu sunucu
  yalnız standart kütüphane kullandığı için onu içe almaz.
- Paylaşılan sunucuda bu araç hiç çalışmaz (kişisel veri: KVKK).
- Ada/parsel/yüzölçümü, okunmuş parsel geometrisiyle karşılaştırılır.
- Şerh/beyan/rehin satırlarındaki anahtar sözcükler hukuki işarete çevrilir.

Belge düzeni gerçek bir örnekle SABİTLENMEDİ: etiket sözlüğü toleranslıdır ve
çıktı, tanıyamadığı satır sayısını ve düşük güveni açıkça bildirir.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from tkgm_parsel import tr_sayi

_TR = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iisSgGuUoOcC")

# Katlanmış etiket → şema alanı. Sıra önemli: uzun etiket kısadan önce denenir.
_ETIKETLER = [
    ("ana tasinmaz nitelik", "nitelik"), ("tasinmaz nitelik", "nitelik"), ("nitelik", "nitelik"),
    ("mahalle/koy", "mahalle"), ("mahalle koy", "mahalle"), ("mahalle", "mahalle"), ("koy", "mahalle"),
    ("ilce", "ilce"), ("il", "il"), ("mevkii", "mevkii"), ("mevki", "mevkii"),
    ("ada no", "ada"), ("ada", "ada"), ("parsel no", "parsel"), ("parsel", "parsel"),
    ("yuzolcumu", "yuzolcumu_m2"), ("yuzolcum", "yuzolcumu_m2"),
    ("zemin tipi", "zemin_tipi"), ("zemin tip", "zemin_tipi"),
    ("cilt/sayfa", "cilt_sayfa"), ("cilt sayfa no", "cilt_sayfa"), ("cilt no", "cilt"), ("sayfa no", "sayfa"),
    ("blok/kat/giris/bbno", "bagimsiz_bolum"), ("bagimsiz bolum no", "bagimsiz_bolum"),
    ("bagimsiz bolum nitelik", "bb_nitelik"), ("arsa payi", "arsa_payi"), ("arsa pay/payda", "arsa_payi"),
    ("pafta no", "pafta"), ("pafta", "pafta"),
]
_MALIK_ETIKETLERI = ("malik", "ad soyad", "adi soyadi", "hissedar", "hak sahibi")
_BOLUMLER = [("serh", "serhler"), ("beyan", "beyanlar"), ("irtifak", "irtifaklar"),
             ("rehin", "rehinler"), ("ipotek", "rehinler")]

# Anahtar sözcük → işaret. Adresler başlangıç noktasıdır; `hukuk_koprusu` gibi, metni
# tr_mevzuat_* ile çekmeden alıntılanmaz. 2026-09-20'de tr_mevzuat_icinde_ara ile açılıp
# bakılanlar: TMK 194, TMK 735, Tapu K. m. 26 (beş yıl), TBK 312. Diğerleri ezberdendir;
# 2942'deki şerh maddesi aramada çıkmadığı için numarasız bırakıldı.
_ISARETLER = [
    ("aile konutu", "Aile konutu şerhi: eşin açık rızası olmadan devir/sınırlama (TMK 194)."),
    ("kamulastirma", "Kamulaştırma şerhi: 2942 s. K. — şerhin süresi ve hükmü için ilgili maddeyi teyit edin."),
    ("ihtiyati tedbir", "İhtiyati tedbir: devir engeli olabilir; kararı veren mahkeme ve dosya no teyit edilmeli."),
    ("haciz", "Haciz / ihtiyati haciz: alacaklı, dosya no ve tarih sırası (İİK)."),
    ("ipotek", "İpotek: derece, tutar, alacaklı; fekki satış koşulu olabilir (TMK 881 vd.)."),
    ("intifa", "İntifa hakkı: çıplak mülkiyet devri değeri düşürür (TMK 794 vd.)."),
    ("satis vaadi", "Satış vaadi şerhi: beş yıl içinde satış yapılmazsa re'sen terkin (Tapu K. m. 26)."),
    ("kira", "Kira şerhi: yeni malike karşı ileri sürülebilir (TBK 312)."),
    ("onalim", "Sözleşmeden doğan önalım şerhi: etkisi şerh tarihinden on yıl sonra sona erer (TMK 735)."),
    ("geri alim", "Geri alım (vefa) şerhi (TMK 736)."),
    ("2/b", "2/B beyanı: 6292 s. K."),
    ("orman", "Orman beyanı/şerhi: 6831 s. K."),
    ("korunmasi gerekli", "Kültür/tabiat varlığı beyanı: 2863 s. K."),
    ("sit alani", "Sit alanı beyanı: 2863 s. K."),
    ("riskli yapi", "Riskli yapı beyanı: 6306 s. K."),
    ("yonetim plani", "Yönetim planı beyanı: 634 s. K."),
]


def _katla(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_TR).lower()).strip()


# Belirlenimci kimlik kalıpları. NLP yok: her biri ya kontrol basamağı ya sabit
# uzunluk taşır, yani eşleşme tahmin değil. Ayraç toleransı şart — "123 456 789 01"
# ile "12345678901" aynı numaradır ve yalnız ikincisini maskelemek maskelememektir.
_AYRAC = r"[ .\-]?"


def _rakam(s: str) -> str:
    return re.sub(r"\D", "", s)


def _tckn_gecerli(s: str) -> bool:
    """NVİ kontrol basamakları. Doğrulamadan maskelemek yevmiye numarasını da yutar."""
    d = [int(c) for c in s]
    if len(d) != 11 or d[0] == 0:
        return False
    t1 = sum(d[0:9:2]) * 7 - sum(d[1:8:2])
    return t1 % 10 == d[9] and sum(d[:10]) % 10 == d[10]


def _iban_gecerli(s: str) -> bool:
    """mod-97. TR IBAN 26 karakterdir."""
    s = s.upper()
    if not re.fullmatch(r"TR\d{24}", s):
        return False
    tasi = s[4:] + s[:4]
    sayi = "".join(str(ord(c) - 55) if c.isalpha() else c for c in tasi)
    return int(sayi) % 97 == 1


_DESENLER = (
    ("TCKN", r"(?<!\d)\d{3}%s\d{3}%s\d{3}%s\d{2}(?!\d)" % (_AYRAC, _AYRAC, _AYRAC),
     lambda h: _tckn_gecerli(_rakam(h)), lambda h: "*" * 9 + _rakam(h)[-2:]),
    ("IBAN", r"(?i)\bTR(?:%s\d{2}){12}\b" % _AYRAC,
     lambda h: _iban_gecerli(_rakam(h) and "TR" + _rakam(h)), lambda h: "TR**...**" + _rakam(h)[-4:]),
    ("TELEFON", r"(?<!\d)(?:\+90|0)%s5\d{2}%s\d{3}%s\d{2}%s\d{2}(?!\d)" % ((_AYRAC,) * 4),
     lambda h: True, lambda h: "*" * 7 + _rakam(h)[-4:]),
    ("EPOSTA", r"[\w.+-]+@[\w-]+\.[\w.]+", lambda h: True,
     lambda h: "***@" + h.split("@", 1)[1]),
)

# VKN'de sağlama basamağı kullanılmıyor: GİB algoritmasını burada doğrulayamadım ve
# doğrulanmamış bir sağlama iki yönden de yanlış yapar — gerçek VKN'yi kaçırır, rastgele
# on haneli sayının onda birini maskeler (yevmiye no, alan). Onun yerine SATIR BAĞLAMI
# aranır: etiketli bir VKN maskelenir, etiketsiz on haneli sayıya dokunulmaz.
_VKN_BAGLAM = re.compile(r"vergi\s*(kimlik)?\s*(no|numaras)|(?<![a-z])vkn(?![a-z])", re.I)
_VKN_SAYI = re.compile(r"(?<!\d)\d{10}(?!\d)")


def _maskele(metin: str) -> str:
    """Kimlik numaralarını, IBAN'ı, telefonu ve e-postayı yıldızlar.

    Kişi ve şirket ADLARI burada maskelenmez: bir adı kalıpla tanımak NLP ister ve
    yanlış tanıma ya masumu yutar ya maliki kaçırır. Adlar `_ad_maskele` ile,
    ayrıştırıcının Malik: satırından ZATEN bildiği dizgeler üzerinden maskelenir.
    """
    for _, desen, gecerli, degistir in _DESENLER:
        metin = re.sub(desen, lambda m: degistir(m.group(0)) if gecerli(m.group(0)) else m.group(0), metin)
    satirlar = []
    for satir in metin.split("\n"):
        if _VKN_BAGLAM.search(_katla(satir)) or _VKN_BAGLAM.search(satir):
            satir = _VKN_SAYI.sub(lambda m: "*" * 8 + m.group(0)[-2:], satir)
        satirlar.append(satir)
    return "\n".join(satirlar)


def _tr_varyant(ad: str) -> List[str]:
    """Aynı adın belgede rastlanan yazımları. Türkçe büyük/küçük harf str.upper() ile
    bozulur ('i'→'I'), bu yüzden dönüşüm elle yapılır."""
    kucuk = ad.translate(str.maketrans("IİÖÜŞĞÇ", "ıiöüşğç")).lower()
    buyuk = ad.translate(str.maketrans("iı", "İI")).upper()
    return list(dict.fromkeys([ad, buyuk, kucuk, kucuk.title()]))


def _ad_maskele(kayit: Dict[str, Any]) -> int:
    """Ayrıştırılmış malik adlarını {{MALİK-nn}} etiketine çevirir — kayıdın HER yerinde.

    Adı yalnız `malikler` listesinde değiştirmek yetmez: aynı ad şerh, beyan ve rehin
    satırlarında da geçer. Eşleştirme döndürülmez; avukat zaten belgeye bakıyordur,
    modele giden ise etikettir.
    """
    adlar = [str(m.get("ad", "")).strip() for m in kayit.get("malikler", [])]
    esleme: List[Tuple[str, str]] = []
    for sira, ad in enumerate(a for a in adlar if len(a) >= 3):
        etiket = "{{MALİK-%02d}}" % (sira + 1)
        # "AHMET ÖRNEK (T.C. *********46)" olduğu gibi aranırsa şerh satırındaki çıplak
        # "AHMET ÖRNEK" kaçar. Parantezli ek ve maskelenmiş kuyruk atılıp çekirdek ad da
        # aranır — testte bu kusur bir kez gerçekten yakalandı.
        cekirdek = re.split(r"[(\[]|\s+T\.?C\.?\s|\s*[*]{3,}", ad)[0].strip(" ,;-")
        for aday in dict.fromkeys([ad, cekirdek]):
            if len(aday) >= 3:
                for v in _tr_varyant(aday):
                    esleme.append((v, etiket))
    # Uzun önce: "AHMET ÖRNEK OĞLU" içindeki "AHMET ÖRNEK" önce değişirse artık kalır.
    esleme.sort(key=lambda x: -len(x[0]))

    def degis(s: str) -> str:
        for v, etiket in esleme:
            s = s.replace(v, etiket)
        return s

    def gez(o: Any) -> Any:
        if isinstance(o, str):
            return degis(o)
        if isinstance(o, list):
            return [gez(x) for x in o]
        if isinstance(o, dict):
            return {k: gez(v) for k, v in o.items()}
        return o

    for k in list(kayit):
        if k not in ("guven", "kvkk"):
            kayit[k] = gez(kayit[k])
    return len({e for _, e in esleme})


def ayristir(metin: str, maskele: bool = True, ad_maskele: bool = True) -> Dict[str, Any]:
    if maskele:
        metin = _maskele(metin)
    tasinmaz: Dict[str, Any] = {}
    malikler: List[Dict[str, str]] = []
    bolumler: Dict[str, List[str]] = {ad: [] for _, ad in _BOLUMLER}
    aktif: Optional[str] = None
    taninmayan = 0

    for ham in metin.splitlines():
        satir = ham.strip()
        if not satir:
            continue
        katli = _katla(satir)

        # Bölüm başlığı: kısa, değer taşımayan, anahtar sözcükle başlayan satır.
        baslik = next((ad for anahtar, ad in _BOLUMLER
                       if katli.startswith(anahtar) and len(katli) < 40 and ":" not in satir), None)
        if baslik:
            aktif = baslik
            continue

        m = re.match(r"^([^:]{2,40}?)\s*[:：]\s*(.+)$", satir)
        if m:
            etiket_k, deger = _katla(m.group(1)), m.group(2).strip()
            if any(etiket_k.startswith(e) for e in _MALIK_ETIKETLERI):
                malikler.append({"ad": deger})
                aktif = None
                continue
            if etiket_k in ("hisse", "hisse pay/payda", "pay/payda", "hisse orani") and malikler:
                malikler[-1]["hisse"] = deger
                continue
            if etiket_k in ("edinme sebebi", "edinme sebep", "iktisap sebebi") and malikler:
                malikler[-1]["edinme_sebebi"] = deger
                continue
            if etiket_k in ("tarih-yevmiye", "tarih/yevmiye", "tarih yevmiye", "yevmiye") and malikler:
                malikler[-1]["tarih_yevmiye"] = deger
                continue
            alan = next((a for e, a in _ETIKETLER if etiket_k == e), None)
            if alan:
                tasinmaz.setdefault(alan, tr_sayi(deger) if alan == "yuzolcumu_m2" else deger)
                aktif = None
                continue
        if aktif:
            bolumler[aktif].append(satir)
        else:
            taninmayan += 1

    tum_kisit = " ".join(s for liste in bolumler.values() for s in liste)
    katli_kisit = _katla(tum_kisit)
    isaretler = [not_ for anahtar, not_ in _ISARETLER if anahtar in katli_kisit]

    kayit: Dict[str, Any] = {
        "tasinmaz": tasinmaz, "malikler": malikler, **bolumler,
        "isaretler": isaretler,
        "taninmayan_satir": taninmayan,
        "guven": "düşük — belge düzeni gerçek bir örnekle sabitlenmedi; her alanı belgeyle karşılaştırın",
    }
    etiketlenen = _ad_maskele(kayit) if ad_maskele else 0
    # Ne maskelendiği kadar ne MASKELENMEDİĞİ de yazılır: "maskelendi" diye okunan
    # bir alan, altında ne olduğu söylenmezse olmayan bir güvence verir.
    kayit["kvkk"] = {
        "maskelenen": (["TCKN", "IBAN", "VKN", "telefon", "e-posta"] if maskele else []) +
                      (["malik adı → {{MALİK-nn}} (%d kişi)" % etiketlenen] if etiketlenen else []),
        "maskelenmeyen": [x for x in (
            None if maskele else "kimlik numaraları (maskele=false)",
            None if ad_maskele else "malik adları (ad_maskele=false)",
            "şerh/beyan/rehin satırlarındaki ÜÇÜNCÜ kişi ve şirket adları (alacaklı banka, kiracı, "
            "mahkeme ve dosya no) — bunlar ayrıştırıcının ad olarak bilmediği dizgelerdir",
            "adres, doğum tarihi",
        ) if x],
        "saklama": "Eşleştirme döndürülmez ve diske yazılmaz; hiçbir şey bellekte tutulmaz.",
        "daha_fazlasi": "Ad/unvan/adres için tam takma adlandırma Arthur Mask'in işidir "
                        "(arthur_mask.servis.maskele_belge); bu sunucu yalnız standart kütüphane "
                        "kullandığı için onu içe almaz.",
    }
    return kayit


def capraz_kontrol(kayit: Dict[str, Any], parsel: Dict[str, Any], hesap_alani: float) -> List[str]:
    """Tapu kaydı ile okunmuş parsel dosyası aynı taşınmazı mı anlatıyor?"""
    t, oz = kayit["tasinmaz"], parsel["oznitelik"]
    out: List[str] = []
    # Tutan alanlar da sayılır: yalnız uyuşmazlığı yazmak, hiç karşılaştırılmamış bir
    # alanla eşleşmiş bir alanı aynı sessizlikte bırakır — ikisi çok farklı şeylerdir.
    tutan: List[str] = []
    for alan in ("ada", "parsel"):
        if t.get(alan) and oz.get(alan):
            if str(t[alan]).strip() != str(oz[alan]).strip():
                out.append("%s UYUŞMUYOR: tapu kaydı %s, parsel dosyası %s." % (alan, t[alan], oz[alan]))
            else:
                tutan.append("%s %s" % (alan, t[alan]))
    for alan in ("il", "ilce", "mahalle"):
        if t.get(alan) and oz.get(alan):
            if _katla(str(t[alan])) not in _katla(str(oz[alan])) \
                    and _katla(str(oz[alan])) not in _katla(str(t[alan])):
                out.append("%s UYUŞMUYOR: tapu kaydı %s, parsel dosyası %s." % (alan, t[alan], oz[alan]))
            else:
                tutan.append("%s %s" % (alan, t[alan]))
    if tutan:
        out.append("Tutan alanlar: %s." % ", ".join(tutan))
    yuz = t.get("yuzolcumu_m2")
    if yuz:
        fark = hesap_alani - yuz
        out.append("Tapu yüzölçümü %.2f m², geometriden hesap alanı %.2f m² (fark %+.2f m², %%%.2f). "
                   "Fark bir işarettir; düzeltme 3402 s. K. m. 41 yoluna ve kadastro kayıtlarına tabidir."
                   % (yuz, hesap_alani, fark, abs(fark) / yuz * 100.0))
    if not out:
        out.append("Karşılaştırılabilir alan bulunamadı: ada, parsel, il/ilçe/mahalle ve yüzölçümünün "
                   "hiçbiri tapu metninde tanınmadı. Belge düzeni beklenenden farklı olabilir.")
    elif not yuz:
        out.append("Yüzölçümü tapu metninde tanınmadı; alan karşılaştırması YAPILMADI.")
    return out
