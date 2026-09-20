"""tkgm_analiz — okunan parseli izdüşürür ve ölçer. Kroki ile araçlar aynı sayıyı
buradan alır; alan iki yerde iki ayrı hesaplanırsa er geç iki ayrı sonuç çıkar."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import tkgm_geo as geo

Nokta = Tuple[float, float]


def tr_bicim(x: float, hane: int = 2) -> str:
    """1312.4 → "1.312,40" (Türkçe binlik ve ondalık ayırıcı)."""
    s = "{:,.{h}f}".format(x, h=hane)
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def izdusur(parsel: Dict[str, Any], dom: Optional[int] = None,
            sistem: str = "tm3") -> Tuple[int, List[List[List[Nokta]]]]:
    """(boylam, enlem) halkalarını (Y sağa, X yukarı) metreye çevirir.

    Dilim, verilmemişse parselin ortalama boylamından seçilir. Komşu parselleri
    aynı krokide çizerken hepsine ana parselin dilimi verilmelidir; yoksa dilim
    sınırındaki iki komşu farklı koordinat sistemlerine düşer.
    """
    duz = [p for c in parsel["cokgenler"] for p in c[0]]
    if dom is None:
        dom = geo.dom_sec(sum(p[0] for p in duz) / len(duz), sistem)
    k0 = geo.SISTEMLER[sistem]["k0"]
    tm = [[[geo.tm_ileri(enlem, boylam, dom, k0) for boylam, enlem in h] for h in c]
          for c in parsel["cokgenler"]]
    return dom, tm


def olc(parsel: Dict[str, Any]) -> Dict[str, Any]:
    dom, tm = izdusur(parsel)
    alan = sum(geo.cokgen_alani(c) for c in tm)
    cevre = sum(geo.cevre(c[0]) for c in tm)
    tapu = parsel["oznitelik"].get("tapu_alani_m2")
    duz = [p for c in parsel["cokgenler"] for p in c[0]]
    out: Dict[str, Any] = {
        "dom": dom,
        "projeksiyon": "ITRF96 TM 3° DOM %d (EPSG:%d)" % (dom, geo.EPSG_TM3[dom]),
        "alan_m2": round(alan, 2),
        "cevre_m": round(cevre, 2),
        "kose_sayisi": sum(len(c[0]) for c in tm),
        "parca_sayisi": len(tm),
        "ic_halka_sayisi": sum(len(c) - 1 for c in tm),
        "merkez": {"enlem": round(sum(p[1] for p in duz) / len(duz), 7),
                   "boylam": round(sum(p[0] for p in duz) / len(duz), 7)},
        "tm": tm,
    }
    belirsizlik = _yuvarlama_belirsizligi(parsel, tm, out["merkez"]["enlem"])
    out.update(belirsizlik)
    if tapu:
        out["tapu_alani_m2"] = tapu
        out["fark_m2"] = round(alan - tapu, 2)
        out["fark_yuzde"] = round((alan - tapu) / tapu * 100.0, 3)
        pay = belirsizlik.get("alan_belirsizligi_m2")
        if pay is not None:
            out["fark_yorumu"] = (
                "Fark, dosyadaki koordinat yuvarlamasının payı (±%s m²) İÇİNDE: kadastro hakkında "
                "bir şey söylemez." % tr_bicim(pay) if abs(alan - tapu) <= pay else
                "Fark, koordinat yuvarlamasının payını (±%s m²) AŞIYOR; yine de yalnız işarettir, "
                "tespit kadastro müdürlüğü kayıtlarıyla yapılır." % tr_bicim(pay))
    return out


def _yuvarlama_belirsizligi(parsel: Dict[str, Any], tm, enlem: float) -> Dict[str, Any]:
    """Dosyadaki koordinat yuvarlamasının alana ve kenar boyuna etkisi (%95, 2σ).

    Gauss alanının bir köşe koordinatına göre türevi komşu köşeler arası farkın yarısıdır;
    yuvarlama hatası [-adım/2, +adım/2]'de düzgün dağılır (σ = adım/√12) ve köşeler
    bağımsızdır. Dolayısıyla Var(A) = ¼·Σ[(Δx)²σ_y² + (Δy)²σ_x²] — tahmin değil, türetme.
    """
    yazim = parsel.get("yazim") or {}
    d = yazim.get("ondalik", 12)
    if yazim.get("birim") == "metre":
        adim_x = adim_y = 10.0 ** -d
    else:
        adim_y = math.radians(10.0 ** -d) * 6367000.0
        adim_x = math.radians(10.0 ** -d) * 6378137.0 * math.cos(math.radians(enlem))
    if max(adim_x, adim_y) < 0.005:
        return {}
    sx2, sy2 = adim_x ** 2 / 12.0, adim_y ** 2 / 12.0
    varyans = 0.0
    for c in tm:
        for h in c:
            n = len(h)
            for i in range(n):
                dx = h[(i + 1) % n][0] - h[i - 1][0]
                dy = h[(i + 1) % n][1] - h[i - 1][1]
                varyans += (dy * dy * sx2 + dx * dx * sy2) / 4.0
    return {
        "koordinat_adimi_m": round(max(adim_x, adim_y), 2),
        "alan_belirsizligi_m2": round(2.0 * math.sqrt(varyans), 1),
        "kenar_belirsizligi_m": round(2.0 * math.sqrt(sx2 + sy2), 2),
        "hassasiyet_notu": "Dosyada koordinatlar %d ondalıkla yazılmış (~%s m ızgara): kenar boyları ve "
                           "alan yaklaşık değerdir." % (d, tr_bicim(max(adim_x, adim_y), 1)),
    }
