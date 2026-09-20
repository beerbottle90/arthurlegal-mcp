"""tkgm_tapu — kullanıcının KENDİ e-Devlet / Web Tapu oturumundan aldığı tapu kaydı
metnini yapılandırır. Bu sunucu hiçbir oturuma girmez, belgeyi kendisi çekmez.

Girdi metindir: PDF'i model kendisi okuyup metnini verir ya da kullanıcı yapıştırır.
Burada PDF çözümleyici yazılmadı; stdlib'le yazılan bir PDF metin çıkarıcı Türkçe
harfleri (ToUnicode CMap) sessizce bozar ve bozuk bir malik adı, okunamayan bir
belgeden daha tehlikelidir.

Katkısı şema, maskeleme, çapraz kontrol ve işarettir:
- T.C. kimlik numaraları varsayılan olarak maskelenir; hiçbir şey belleğe alınmaz
  (kişisel veri: KVKK). Paylaşılan sunucuda bu araç hiç çalışmaz.
- Ada/parsel/yüzölçümü, okunmuş parsel geometrisiyle karşılaştırılır.
- Şerh/beyan/rehin satırlarındaki anahtar sözcükler hukuki işarete çevrilir.

Belge düzeni gerçek bir örnekle SABİTLENMEDİ: etiket sözlüğü toleranslıdır ve
çıktı, tanıyamadığı satır sayısını ve düşük güveni açıkça bildirir.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

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


def _maskele(metin: str) -> str:
    """11 haneli T.C. kimlik numarasının son iki hanesi dışını yıldızlar."""
    return re.sub(r"(?<!\d)(\d{9})(\d{2})(?!\d)", lambda m: "*" * 9 + m.group(2), metin)


def ayristir(metin: str, maskele: bool = True) -> Dict[str, Any]:
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

    return {
        "tasinmaz": tasinmaz, "malikler": malikler, **bolumler,
        "isaretler": isaretler,
        "taninmayan_satir": taninmayan,
        "guven": "düşük — belge düzeni gerçek bir örnekle sabitlenmedi; her alanı belgeyle karşılaştırın",
        "kvkk": "Malik bilgisi kişisel veridir: bellekte tutulmadı, T.C. kimlik no %s."
                % ("maskelendi" if maskele else "MASKELENMEDİ"),
    }


def capraz_kontrol(kayit: Dict[str, Any], parsel: Dict[str, Any], hesap_alani: float) -> List[str]:
    """Tapu kaydı ile okunmuş parsel dosyası aynı taşınmazı mı anlatıyor?"""
    t, oz = kayit["tasinmaz"], parsel["oznitelik"]
    out: List[str] = []
    for alan in ("ada", "parsel"):
        if t.get(alan) and oz.get(alan) and str(t[alan]).strip() != str(oz[alan]).strip():
            out.append("%s uyuşmuyor: tapu kaydı %s, parsel dosyası %s." % (alan, t[alan], oz[alan]))
    for alan in ("il", "ilce", "mahalle"):
        if t.get(alan) and oz.get(alan) and _katla(str(t[alan])) not in _katla(str(oz[alan])) \
                and _katla(str(oz[alan])) not in _katla(str(t[alan])):
            out.append("%s uyuşmuyor: tapu kaydı %s, parsel dosyası %s." % (alan, t[alan], oz[alan]))
    yuz = t.get("yuzolcumu_m2")
    if yuz:
        fark = hesap_alani - yuz
        out.append("Tapu yüzölçümü %.2f m², geometriden hesap alanı %.2f m² (fark %+.2f m², %%%.2f). "
                   "Fark bir işarettir; düzeltme 3402 s. K. m. 41 yoluna ve kadastro kayıtlarına tabidir."
                   % (yuz, hesap_alani, fark, abs(fark) / yuz * 100.0))
    if not out:
        out.append("Karşılaştırılabilir alan bulunamadı (ada/parsel/yüzölçümü tapu metninde tanınmadı).")
    return out
