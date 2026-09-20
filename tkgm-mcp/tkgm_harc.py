"""tkgm_harc — tapu harcı ve TKGM döner sermaye hizmet bedeli ön hesabı.

Bu modülde TEK BİR RAKAM YOKTUR. Bütün oran ve tutarlar `veri/tarife_2026.json`dan
gelir; o dosyadaki her satır açılıp okunmuş bir kaynaktan birebir alıntı taşır
(TKGM 2026 döner sermaye cetveli, 492 s. K. (4) sayılı tarife). Hesap çıktısı da
her kalemin yanında dayanağını ve alıntısını verir: avukatın müvekkile yazacağı
tutarın nereden geldiği görünür olmalıdır. Yıl değişince kod değil veri değişir.

Yöresel katsayı veride YOKTUR (cetvelde yer almaz, TKGM ayrı yayımlar). Tapu
işlemlerinin ve "YK" işaretli kadastro kalemlerinin bedeli onsuz hesaplanamaz;
araç katsayıyı uydurmaz, ister.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

_TR = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iisSgGuUoOcC")
_YOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veri", "tarife_2026.json")

YK_KAYNAGI = ("Yöresel katsayıyı ilgili tapu müdürlüğü için TKGM Döner Sermaye İşletmesi Müdürlüğü'nün "
              "'Fiyat Listesi – Yöresel Katsayılar' sayfasından alın (www.tkgm.gov.tr › Döner Sermaye "
              "› Mevzuat). LİHKAB il katsayıları bu katsayı DEĞİLDİR.")
ON_HESAP = ("Ön hesaptır; tahakkuk tapu müdürlüğünce yapılır. Nispi oranlar Cumhurbaşkanı kararıyla, "
            "maktu tutarlar her yıl tebliğle değişir — veri sürümünü kontrol edin.")


class HarcHatasi(ValueError):
    """İstenen kalem hesaplanamıyor ya da girdi eksik."""


def _yukle() -> Dict[str, Any]:
    try:
        with open(_YOL, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        return {"surum": None, "doner_sermaye": [], "tapu_harci": [], "kaynaklar": [],
                "uyarilar": [], "yukleme_hatasi": "%s: %s" % (type(exc).__name__, exc)}


_VERI = _yukle()
_DS = {r["kod"]: r for r in _VERI["doner_sermaye"]}
_TH = {r["kod"]: r for r in _VERI["tapu_harci"]}


def _katla(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).translate(_TR).lower())


def _isaret(r: Dict[str, Any]) -> str:
    """Cetvelin kendi sütunu: YK = yöresel katsayıyla çarpılır, M = maktu."""
    if " | YK | " in r["alinti"]:
        return "YK"
    if " | M | " in r["alinti"]:
        return "M"
    return "formül" if r["tutar_tl"] is None else "—"


def durum() -> Dict[str, Any]:
    return {"surum": _VERI.get("surum"), "derlenme": _VERI.get("derlenme_tarihi"),
            "doner_sermaye_satiri": len(_DS), "tapu_harci_satiri": len(_TH),
            "hata": _VERI.get("yukleme_hatasi")}


def ara(sorgu: str, sinir: int = 12) -> List[Dict[str, Any]]:
    sozcukler = _katla(sorgu).split()
    out: List[Dict[str, Any]] = []
    for r in _VERI["tapu_harci"]:
        if all(s in _katla(r["islem"] + " " + r["kod"]) for s in sozcukler):
            deger = ("binde %s" % r["oran_binde"] if r.get("oran_binde") is not None
                     else "%s TL" % r["maktu_tl"] if r.get("maktu_tl") is not None else r["tur"])
            out.append({"tablo": "tapu_harci", "kod": r["kod"], "islem": r["islem"], "deger": deger,
                        "dayanak": r["dayanak"]})
    for r in _VERI["doner_sermaye"]:
        if all(s in _katla(r["islem"] + " " + r["kod"]) for s in sozcukler):
            out.append({"tablo": "doner_sermaye", "kod": r["kod"], "islem": r["islem"],
                        "deger": "%s TL" % r["tutar_tl"] if r["tutar_tl"] is not None else "formül",
                        "isaret": _isaret(r), "birim": r.get("birim")})
    return out[:sinir]


def tapu_harci(kod: str, bedel: float, emlak_vergi_degeri: Optional[float] = None) -> Dict[str, Any]:
    r = _TH.get(kod)
    if r is None or r["tur"] not in ("nispi", "maktu"):
        raise HarcHatasi("Tapu harcı kodu bulunamadı ya da hesaplanabilir değil: %s. `tarife_ara` ile arayın." % kod)
    out: Dict[str, Any] = {"islem": r["islem"], "dayanak": r["dayanak"], "alinti": r["alinti"][:240]}
    if r["tur"] == "maktu":
        out.update({"kalemler": [{"kalem": r["islem"], "tutar_tl": r["maktu_tl"]}], "toplam_tl": r["maktu_tl"]})
        return out
    if r.get("oran_binde") is None:
        raise HarcHatasi("Bu satır oran taşımıyor (kural/indirim satırı): %s" % r["islem"])
    if not bedel or bedel <= 0:
        raise HarcHatasi("Nispi harç için `bedel` (TL) gerekli.")
    matrah = float(bedel)
    notlar: List[str] = []
    alt = _TH.get("matrah_alt_siniri")
    if emlak_vergi_degeri and emlak_vergi_degeri > matrah:
        matrah = float(emlak_vergi_degeri)
        notlar.append("Beyan edilen bedel emlak vergisi değerinin altında; matrah emlak vergisi değerine "
                      "çekildi (%s)." % (alt["dayanak"] if alt else "492 s. K. m. 63"))
    elif emlak_vergi_degeri is None and alt:
        notlar.append("Emlak vergisi değeri verilmedi: matrah ondan düşük olamaz (%s)." % alt["dayanak"])
    tutar = round(matrah * r["oran_binde"] / 1000.0, 2)
    asgari = _TH.get("asgari_nispi_harc")
    if asgari and asgari.get("maktu_tl") and tutar < asgari["maktu_tl"]:
        tutar = asgari["maktu_tl"]
        notlar.append("Asgari nispi harç uygulandı (%s)." % asgari["dayanak"])
    yukumlu = r.get("yukumlu") or "yükümlü tarifede belirtilmemiş; 492 s. K. m. 58"
    if "ayrı ayrı" in yukumlu:
        kalemler = [{"kalem": "devir eden", "tutar_tl": tutar}, {"kalem": "devir alan", "tutar_tl": tutar}]
    else:
        kalemler = [{"kalem": yukumlu, "tutar_tl": tutar}]
    out.update({"matrah_tl": matrah, "oran_binde": r["oran_binde"], "kanuni_oran": r.get("kanuni_miktar"),
                "kalemler": kalemler, "toplam_tl": round(sum(k["tutar_tl"] for k in kalemler), 2)})
    if notlar:
        out["notlar"] = notlar
    return out


def _carpan(kosul: str, adet: int) -> Optional[Dict[str, float]]:
    """Cetveldeki formül kalıbı → (işlem ücreti adedi, ilave işlem ücreti adedi). Tanınmayan kalıp None."""
    k = _katla(kosul)
    if k.startswith("bir adet islem ucreti") or k.startswith("her bir islem icin bir adet"):
        return {"islem": 1, "ilave": 0}
    if k.startswith("iki adet islem ucreti") or k.startswith("islem ucretinin iki kati"):
        return {"islem": 2, "ilave": 0}
    if k.startswith("islem ucretinin dort kati"):
        return {"islem": 4, "ilave": 0}
    if k.startswith("bagimsiz bolum adedi"):
        return {"islem": adet, "ilave": 0}
    if k.startswith("bir islem ucreti +") and "ilave islem ucreti" in k:
        return {"islem": 1, "ilave": max(0, adet - 1)}
    return None


def doner_sermaye(kod: str, yoresel_katsayi: Optional[float] = None, adet: int = 1) -> Dict[str, Any]:
    r = _DS.get(kod)
    if r is None:
        raise HarcHatasi("Döner sermaye kodu bulunamadı: %s. `tarife_ara` ile arayın." % kod)
    isaret = _isaret(r)
    out: Dict[str, Any] = {"kod": kod, "islem": r["islem"], "isaret": isaret, "birim": r.get("birim"),
                           "kosul": r.get("kosul"), "sayfa": r.get("sayfa"), "alinti": r["alinti"][:240]}
    if isaret == "M":
        out["toplam_tl"] = round(r["tutar_tl"] * adet, 2)
        return out
    if yoresel_katsayi is None:
        out["eksik"] = "yoresel_katsayi"
        out["not"] = YK_KAYNAGI
        return out
    if not (0.1 <= yoresel_katsayi <= 10.0):
        raise HarcHatasi("yoresel_katsayi makul aralıkta değil (0,1–10).")
    if isaret == "YK":
        out["toplam_tl"] = round(r["tutar_tl"] * yoresel_katsayi * adet, 2)
        out["hesap"] = "%s × YK %s × %d" % (r["tutar_tl"], yoresel_katsayi, adet)
        return out
    gosterge, ilave = _DS.get("GOSTERGE"), _DS.get("ILAVE-GOSTERGE")
    if isaret == "formül" and gosterge and ilave and kod[:1] == "1":
        iu = round(gosterge["tutar_tl"] * yoresel_katsayi, 2)
        iiu = round(ilave["tutar_tl"] * yoresel_katsayi, 2)
        out.update({"islem_ucreti_tl": iu, "ilave_islem_ucreti_tl": iiu})
        c = _carpan(r.get("kosul") or "", adet)
        if c:
            out["toplam_tl"] = round(c["islem"] * iu + c["ilave"] * iiu, 2)
            out["hesap"] = "%d × işlem ücreti + %d × ilave işlem ücreti" % (c["islem"], c["ilave"])
        else:
            out["not"] = "Formül kalıbı tanınmadı; `kosul` metnini birim ücretlerle elle uygulayın."
        return out
    out["not"] = "Bu satır kural/tanım satırıdır; tutar üretmez."
    return out
