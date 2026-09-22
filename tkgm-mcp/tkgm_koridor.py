"""tkgm_koridor — güzergâh (hat + genişlik) ile parsellerin kesişimi.

İrtifak/kamulaştırma hesabının ham verisi: koridor her parselden kaç m² kesiyor,
eksen parselin içinden kaç metre geçiyor.

Genel çokgen kırpma yazılmadı; shapely'siz kırpma, tam da hukuki sonucu olan
kenar durumlarında (teğet kenar, çakışan köşe) kırılgandır. Yerine yarı-analitik
tarama kullanılır: yatay tarama çizgilerinde sağa-değer doğrultusu TAM çözülür
(parsel aralıkları kenar kesişimlerinden, kapsül aralığı kapalı formülden —
noktanın doğru parçasına uzaklığı dışbükey olduğundan her tarama çizgisinde tek
aralıktır), yalnız yukarı-değer doğrultusu sayısaldır. O doğrultuda integral
OLAY NOKTALARINDA bölünür: parsel köşeleri, kapsül kenarlarının uçları, kapsül
kenarı–parsel kenarı ve kapsül kenarı–kapsül kenarı kesişimleri, daire–parsel
kenarı kesişimleri. Aralar düzgündür ve Gauss-Legendre ile integre edilir. Orta
nokta taraması bunu yapmıyordu: doğu-batı yönlü bir koridor kenarı dar bir parseli
W·tanθ kalınlığında bir bantta keser, 0,5 m adım o bandı atlar ve alan %5-14
sapar. Sayısal hata 4 ve 2 noktalı kuralın farkından ÖLÇÜLÜR; parselin kendi
alanıyla kıyas koridor kenarının hatasını hiç göremezdi.

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

_AZAMI_PARCA_M = 2.0        # olay aralığı bundan uzunsa bölünür
_AZAMI_PARCA_SAYISI = 6000  # parsel başına; aşılırsa parça boyu büyür
_GL4 = ((-0.8611363115940526, 0.3478548451374538), (-0.3399810435848563, 0.6521451548625461),
        (0.3399810435848563, 0.6521451548625461), (0.8611363115940526, 0.3478548451374538))
_GL2 = ((-0.5773502691896257, 1.0), (0.5773502691896257, 1.0))


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


def _parca_kesisim_v(p1: Nokta, p2: Nokta, q1: Nokta, q2: Nokta) -> Optional[float]:
    """İki doğru parçasının kesişim noktasının v'si; kesişmiyorsa None."""
    dx, dy, ex, ey = p2[0] - p1[0], p2[1] - p1[1], q2[0] - q1[0], q2[1] - q1[1]
    payda = dx * ey - dy * ex
    if abs(payda) < 1e-15:
        return None
    t = ((q1[0] - p1[0]) * ey - (q1[1] - p1[1]) * ex) / payda
    u = ((q1[0] - p1[0]) * dy - (q1[1] - p1[1]) * dx) / payda
    if -1e-12 <= t <= 1 + 1e-12 and -1e-12 <= u <= 1 + 1e-12:
        return p1[1] + t * dy
    return None


def _daire_parca_v(c: Nokta, r: float, p1: Nokta, p2: Nokta) -> List[float]:
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    fx, fy = p1[0] - c[0], p1[1] - c[1]
    a = dx * dx + dy * dy
    if a < 1e-18:
        return []
    b, cc = 2 * (fx * dx + fy * dy), fx * fx + fy * fy - r * r
    d = b * b - 4 * a * cc
    if d < 0:
        return []
    kok = math.sqrt(d)
    return [p1[1] + t * dy for t in ((-b - kok) / (2 * a), (-b + kok) / (2 * a)) if 0.0 <= t <= 1.0]


def _olaylar(halkalar: Sequence[Sequence[Nokta]], parcalar: List[Tuple[Nokta, Nokta]],
             r: float, v0: float, v1: float) -> List[float]:
    """İntegrandın düzgünlüğünü bozan bütün v'ler. Aralarında integrand düzgündür."""
    kenarlar = [(h[i], h[(i + 1) % len(h)]) for h in halkalar for i in range(len(h))]
    vs = {v0, v1}
    vs.update(pt[1] for h in halkalar for pt in h)
    yanlar = []
    for a, b in parcalar:
        for cu, cv in (a, b):
            vs.update((cv - r, cv, cv + r))
            for k1, k2 in kenarlar:
                vs.update(_daire_parca_v((cu, cv), r, k1, k2))
        boy = math.dist(a, b)
        if boy > 1e-9:
            nu, nv = -(b[1] - a[1]) / boy * r, (b[0] - a[0]) / boy * r
            for sg in (1.0, -1.0):
                y = ((a[0] + sg * nu, a[1] + sg * nv), (b[0] + sg * nu, b[1] + sg * nv))
                yanlar.append(y)
                vs.update((y[0][1], y[1][1]))
    for y1, y2 in yanlar:
        for k1, k2 in kenarlar:
            v = _parca_kesisim_v(y1, y2, k1, k2)
            if v is not None:
                vs.add(v)
    if len(yanlar) <= 400:
        for i in range(len(yanlar)):
            for j in range(i + 1, len(yanlar)):
                v = _parca_kesisim_v(yanlar[i][0], yanlar[i][1], yanlar[j][0], yanlar[j][1])
                if v is not None:
                    vs.add(v)
    return sorted(v for v in vs if v0 <= v <= v1)


def _parsel_kesisimi(cokgenler: Sequence[Sequence[Sequence[Nokta]]],
                     hatlar: Sequence[Sequence[Nokta]], r: float) -> Dict[str, float]:
    """Tek parsel (tek dilimde izdüşürülmüş) için kesişim alanı, eksen boyu ve sayısal hata."""
    alan = alan2 = eksen = gauss = 0.0
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
            continue
        for a, b in parcalar:
            eksen += _eksen_boyu(a, b, c)

        def f(v: float) -> float:
            ic = _cokgen_araliklari(c, v)
            if not ic:
                return 0.0
            kapsuller = [x for x in (_kapsul_araligi(a, b, r, v) for a, b in parcalar) if x]
            return _kesisim_boyu(ic, _birlestir(kapsuller)) if kapsuller else 0.0

        olaylar = _olaylar(c, parcalar, r, v0, v1)
        parca_boyu = max(_AZAMI_PARCA_M, (v1 - v0) / _AZAMI_PARCA_SAYISI)
        for va, vb in zip(olaylar, olaylar[1:]):
            boy = vb - va
            if boy <= 1e-12:
                continue
            k = max(1, int(math.ceil(boy / parca_boyu)))
            h = boy / k
            for j in range(k):
                orta = va + (j + 0.5) * h
                alan += sum(w * f(orta + x * h / 2) for x, w in _GL4) * h / 2
                alan2 += sum(w * f(orta + x * h / 2) for x, w in _GL2) * h / 2
    hata = abs(alan - alan2) / alan * 100.0 if alan > 1e-9 else 0.0
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


def _csv_alan(v: Any) -> str:
    """Parsel dosyasından gelen metin: "=HYPERLINK(...)" Excel'de çalışmasın, ";" ve satır
    sonu sütun/satır uydurmasın (RFC 4180 tırnaklama + OWASP formül önlemi)."""
    s = re.sub(r"[\r\n]+", " ", str(v if v is not None else ""))
    if s[:1] in ("=", "+", "-", "@", "\t"):
        s = "'" + s
    return '"%s"' % s.replace('"', '""')


def csv_yaz(sonuc: Dict[str, Any]) -> str:
    def sayi(x: float) -> str:
        return ("%.2f" % x).replace(".", ",")

    out = ["\ufeffparsel;nitelik;parsel_alani_m2;kesisim_m2;oran_yuzde;eksen_m;eksen_geciyor;ref"]
    for s in sonuc["satirlar"]:
        out.append(";".join([_csv_alan(s["parsel"]), _csv_alan(s["nitelik"]), sayi(s["parsel_alani_m2"]),
                             sayi(s["kesisim_m2"]), sayi(s["oran_yuzde"]), sayi(s["eksen_m"]),
                             "evet" if s["eksen_geciyor"] else "hayır", _csv_alan(s["ref"])]))
    return "\n".join(out) + "\n"
