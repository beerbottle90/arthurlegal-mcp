"""tkgm_koridor — güzergâh (hat + genişlik) ile parsellerin kesişimi.

İrtifak/kamulaştırma hesabının ham verisi: koridor her parselden kaç m² kesiyor,
eksen parselin içinden kaç metre geçiyor.

Genel çokgen kırpma yazılmadı; shapely'siz kırpma, tam da hukuki sonucu olan
kenar durumlarında (teğet kenar, çakışan köşe) kırılgandır. Yerine yarı-analitik
tarama kullanılır: yatay tarama çizgilerinde sağa-değer doğrultusu TAM çözülür
(parsel aralıkları kenar kesişimlerinden, kapsül aralığı kapalı formülden —
noktanın doğru parçasına uzaklığı dışbükey olduğundan her tarama çizgisinde tek
aralıktır), yalnız yukarı-değer doğrultusu sayısaldır. Aynı taramayla parselin
kendi alanı da hesaplanıp Gauss alanıyla karşılaştırılır; böylece her sonuç
yanında ÖLÇÜLMÜŞ bir sayısal hata taşır, varsayılmış değil.

Her parsel kendi TM 3° diliminde hesaplanır ve hat o dilime izdüşürülür: uzun bir
hat birkaç dilim geçer, tek dilimde hesap komşu dilimdeki alanları ~%0,16 şişirir.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Sequence, Tuple

import tkgm_geo as geo
from tkgm_analiz import izdusur
from tkgm_parsel import etiket

Nokta = Tuple[float, float]
Aralik = Tuple[float, float]

_AZAMI_TARAMA = 4000


class KoridorHatasi(ValueError):
    """Hat okunamadı ya da girdi tutarsız."""


# --------------------------------------------------------------------------- #
def hat_oku(metin: str) -> List[List[Nokta]]:
    """GeoJSON (Multi)LineString ya da KML LineString → [(boylam, enlem)…] parçaları."""
    govde = metin.lstrip("﻿ \t\r\n")
    parcalar: List[List[Nokta]] = []
    if govde.startswith("{"):
        def gez(v: Any) -> None:
            if not isinstance(v, dict):
                return
            tur = v.get("type")
            if tur == "FeatureCollection":
                for f in v.get("features") or []:
                    gez(f)
            elif tur == "Feature":
                gez(v.get("geometry"))
            elif tur == "LineString":
                parcalar.append([(float(p[0]), float(p[1])) for p in v.get("coordinates") or []])
            elif tur == "MultiLineString":
                for c in v.get("coordinates") or []:
                    parcalar.append([(float(p[0]), float(p[1])) for p in c])
        try:
            gez(json.loads(govde))
        except ValueError as exc:
            raise KoridorHatasi("Geçerli JSON değil: %s" % exc) from exc
    elif govde.startswith("<"):
        if re.search(r"<!\s*(DOCTYPE|ENTITY)", govde, re.I):
            raise KoridorHatasi("DOCTYPE/ENTITY içeren KML reddedildi.")
        try:
            kok = ET.fromstring(govde.encode("utf-8"))
        except ET.ParseError as exc:
            raise KoridorHatasi("Geçerli KML değil: %s" % exc) from exc
        for el in kok.iter():
            el.tag = el.tag.split("}", 1)[-1]
        for ls in kok.iter("LineString"):
            ham = (ls.findtext("coordinates") or "").split()
            parcalar.append([(float(t.split(",")[0]), float(t.split(",")[1])) for t in ham])
    else:
        raise KoridorHatasi("Hat biçimi tanınmadı: GeoJSON LineString ya da KML bekleniyor.")
    parcalar = [p for p in parcalar if len(p) >= 2]
    if not parcalar:
        raise KoridorHatasi("Dosyada LineString yok.")
    return parcalar


def hat_dogrula(parcalar: Sequence[Sequence[Nokta]]) -> None:
    for p in parcalar:
        for boylam, enlem in p:
            if not (25.0 <= boylam <= 45.5 and 35.0 <= enlem <= 43.0):
                raise KoridorHatasi(
                    "Hat noktası Türkiye dışında (enlem %.5f, boylam %.5f). `hat` dizisinde sıra "
                    "[enlem, boylam]; GeoJSON'da [boylam, enlem]." % (enlem, boylam))


# --------------------------------------------------------------------------- #
def _birlestir(araliklar: List[Aralik]) -> List[Aralik]:
    araliklar.sort()
    out: List[Aralik] = []
    for a, b in araliklar:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def _kesisim_boyu(p: List[Aralik], q: List[Aralik]) -> float:
    i = j = 0
    toplam = 0.0
    while i < len(p) and j < len(q):
        a, b = max(p[i][0], q[j][0]), min(p[i][1], q[j][1])
        if b > a:
            toplam += b - a
        if p[i][1] < q[j][1]:
            i += 1
        else:
            j += 1
    return toplam


def _cokgen_araliklari(halkalar: Sequence[Sequence[Nokta]], v: float) -> List[Aralik]:
    """v = sabit tarama çizgisinin çokgen içindeki aralıkları (çift-tek kuralı, iç halkalar dâhil)."""
    kesisim: List[float] = []
    for h in halkalar:
        n = len(h)
        for i in range(n):
            (u1, v1), (u2, v2) = h[i], h[(i + 1) % n]
            if (v1 > v) != (v2 > v):
                kesisim.append(u1 + (v - v1) * (u2 - u1) / (v2 - v1))
    kesisim.sort()
    return [(kesisim[k], kesisim[k + 1]) for k in range(0, len(kesisim) - 1, 2)]


def _dogrusal_kisit(alt: float, ust: float, katsayi: float, sabit: float) -> Optional[Aralik]:
    """alt ≤ katsayi·u + sabit ≤ ust koşulunu sağlayan u aralığı; boşsa None."""
    if abs(katsayi) < 1e-12:
        return (-math.inf, math.inf) if alt <= sabit <= ust else None
    a, b = (alt - sabit) / katsayi, (ust - sabit) / katsayi
    return (a, b) if a <= b else (b, a)


def _kapsul_araligi(a: Nokta, b: Nokta, r: float, v: float) -> Optional[Aralik]:
    """Tarama çizgisinin, [a,b] parçasının r yarıçaplı kapsülü içindeki (tek) aralığı."""
    dusuk, yuksek = math.inf, -math.inf
    for cu, cv in (a, b):
        dv = v - cv
        if abs(dv) <= r:
            yari = math.sqrt(r * r - dv * dv)
            dusuk, yuksek = min(dusuk, cu - yari), max(yuksek, cu + yari)
    boy = math.dist(a, b)
    if boy > 1e-9:
        du, dvv = (b[0] - a[0]) / boy, (b[1] - a[1]) / boy
        # p = (u, v);  s = (p-a)·d ∈ [0, boy],  t = (p-a)·n ∈ [-r, r],  n = (-dvv, du)
        s = _dogrusal_kisit(0.0, boy, du, -a[0] * du + (v - a[1]) * dvv)
        t = _dogrusal_kisit(-r, r, -dvv, a[0] * dvv + (v - a[1]) * du)
        if s is not None and t is not None:
            lo, hi = max(s[0], t[0]), min(s[1], t[1])
            if lo <= hi and math.isfinite(lo) and math.isfinite(hi):
                dusuk, yuksek = min(dusuk, lo), max(yuksek, hi)
    return (dusuk, yuksek) if dusuk <= yuksek else None


def _eksen_boyu(a: Nokta, b: Nokta, halkalar: Sequence[Sequence[Nokta]]) -> float:
    """[a,b] parçasının çokgen içinde kalan boyu: kenar kesişimlerinde böl, orta noktaları sına."""
    ts = [0.0, 1.0]
    dx, dy = b[0] - a[0], b[1] - a[1]
    for h in halkalar:
        n = len(h)
        for i in range(n):
            (x1, y1), (x2, y2) = h[i], h[(i + 1) % n]
            ex, ey = x2 - x1, y2 - y1
            payda = dx * ey - dy * ex
            if abs(payda) < 1e-12:
                continue
            t = ((x1 - a[0]) * ey - (y1 - a[1]) * ex) / payda
            s = ((x1 - a[0]) * dy - (y1 - a[1]) * dx) / payda
            if 0.0 <= t <= 1.0 and 0.0 <= s <= 1.0:
                ts.append(t)
    ts.sort()
    boy = math.hypot(dx, dy)
    toplam = 0.0
    for t0, t1 in zip(ts, ts[1:]):
        if t1 - t0 > 1e-12:
            tm = (t0 + t1) / 2.0
            if geo.cokgen_icinde((a[0] + tm * dx, a[1] + tm * dy), halkalar):
                toplam += (t1 - t0) * boy
    return toplam


def _parsel_kesisimi(cokgenler: Sequence[Sequence[Sequence[Nokta]]],
                     hatlar: Sequence[Sequence[Nokta]], r: float) -> Dict[str, float]:
    """Tek parsel (tek dilimde izdüşürülmüş) için kesişim alanı, eksen boyu ve sayısal hata."""
    alan = eksen = tarama_alani = gauss = 0.0
    for c in cokgenler:
        gauss += geo.cokgen_alani(c)
        u0, v0, u1, v1 = geo.sinir_kutusu(c[0])
        parcalar = []
        for hat in hatlar:
            for a, b in zip(hat, hat[1:]):
                if (max(a[0], b[0]) + r >= u0 and min(a[0], b[0]) - r <= u1
                        and max(a[1], b[1]) + r >= v0 and min(a[1], b[1]) - r <= v1):
                    parcalar.append((a, b))
        if not parcalar:
            tarama_alani += geo.cokgen_alani(c)     # taranmadı; hata ölçüsüne nötr katkı
            continue
        for a, b in parcalar:
            eksen += _eksen_boyu(a, b, c)
        adim = min(0.5, max(0.02, (v1 - v0) / 2000.0))
        n = min(_AZAMI_TARAMA, max(1, int(math.ceil((v1 - v0) / adim))))
        adim = (v1 - v0) / n
        for k in range(n):
            v = v0 + (k + 0.5) * adim
            ic = _cokgen_araliklari(c, v)
            if not ic:
                continue
            tarama_alani += sum(b - a for a, b in ic) * adim
            kapsuller = [x for x in (_kapsul_araligi(a, b, r, v) for a, b in parcalar) if x]
            if kapsuller:
                alan += _kesisim_boyu(ic, _birlestir(kapsuller)) * adim
    hata = abs(tarama_alani - gauss) / gauss * 100.0 if gauss > 0 else 0.0
    return {"alan": alan, "eksen": eksen, "parsel_alani": gauss, "hata_yuzde": hata}


def kesisim(parseller: Sequence[Dict[str, Any]], hatlar_cografi: Sequence[Sequence[Nokta]],
            genislik_m: float) -> Dict[str, Any]:
    """Her parsel için koridor kesişimi. `hatlar_cografi`: [(boylam, enlem)…] parçaları."""
    if not (0.1 <= genislik_m <= 2000.0):
        raise KoridorHatasi("genislik_m 0,1 ile 2000 m arasında olmalı.")
    hat_dogrula(hatlar_cografi)
    r = genislik_m / 2.0
    izdusum_bellegi: Dict[int, List[List[Nokta]]] = {}
    satirlar: List[Dict[str, Any]] = []
    en_kotu_hata = 0.0
    for p in parseller:
        dom, tm = izdusur(p)
        if dom not in izdusum_bellegi:
            izdusum_bellegi[dom] = [[geo.tm_ileri(enlem, boylam, dom) for boylam, enlem in h]
                                    for h in hatlar_cografi]
        s = _parsel_kesisimi(tm, izdusum_bellegi[dom], r)
        if s["alan"] <= 0.005 and s["eksen"] <= 0.005:
            continue
        en_kotu_hata = max(en_kotu_hata, s["hata_yuzde"])
        satirlar.append({
            "ref": p["ref"], "parsel": etiket(p), "nitelik": p["oznitelik"].get("nitelik", ""),
            "parsel_alani_m2": round(s["parsel_alani"], 2),
            "kesisim_m2": round(s["alan"], 2),
            "oran_yuzde": round(s["alan"] / s["parsel_alani"] * 100.0, 2) if s["parsel_alani"] else 0.0,
            "eksen_m": round(s["eksen"], 2),
            "eksen_geciyor": s["eksen"] > 0.005,
        })
    satirlar.sort(key=lambda x: -x["kesisim_m2"])
    # Hat boyu, parçanın kendi diliminde: eksen uzunluğu da projeksiyon düzlemindedir.
    hat_boyu = 0.0
    for h in hatlar_cografi:
        dom = geo.dom_sec(sum(pt[0] for pt in h) / len(h))
        tm = [geo.tm_ileri(enlem, boylam, dom) for boylam, enlem in h]
        hat_boyu += sum(math.dist(a, b) for a, b in zip(tm, tm[1:]))
    return {
        "genislik_m": genislik_m, "hat_boyu_m": round(hat_boyu, 2),
        "incelenen_parsel": len(parseller), "etkilenen_parsel": len(satirlar),
        "toplam_kesisim_m2": round(sum(x["kesisim_m2"] for x in satirlar), 2),
        "sayisal_hata_yuzde_en_kotu": round(en_kotu_hata, 4),
        "satirlar": satirlar,
    }


def csv_yaz(sonuc: Dict[str, Any]) -> str:
    out = ["parsel;nitelik;parsel_alani_m2;kesisim_m2;oran_yuzde;eksen_m;eksen_geciyor;ref"]
    for s in sonuc["satirlar"]:
        out.append("%s;%s;%.2f;%.2f;%.2f;%.2f;%s;%s" % (
            s["parsel"], s["nitelik"], s["parsel_alani_m2"], s["kesisim_m2"], s["oran_yuzde"],
            s["eksen_m"], "evet" if s["eksen_geciyor"] else "hayır", s["ref"]))
    return "\n".join(out) + "\n"
