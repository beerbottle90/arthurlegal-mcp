"""tkgm_geo — jeodezi ve düzlem geometrisi, yalnız standart kütüphane.

Kadastro alanı elipsoit üzerinde değil, **projeksiyon düzleminde** hesaplanır:
köşeler ITRF96 / TM 3° dilimine izdüşürülür, alan o koordinatlardan Gauss
(shoelace) ile çıkar. TKGM'nin "alan" özniteliğini tutturmanın yolu da budur;
küresel/jeodezik alan formülleri bilerek kullanılmadı, çünkü başka bir sayı
verirler ve o sayının tapuda karşılığı yoktur.

İzdüşüm Krüger serisidir (n⁴ mertebesi). n³'te kesildiğinde gidiş-dönüş hatası
dilim kenarında 0,2 mm ölçüldü; n⁴ ile mikrometre altına iner. İkisi de kadastro
ölçüsünün doğruluğunun ötesindedir, ama testin "eşit" diyebilmesi için n⁴ tutuldu.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Nokta = Tuple[float, float]

# GRS80 — ITRF96/TUREF'in elipsoidi. WGS84 ile farkı yarı küçük eksende 0,1 mm.
_A = 6378137.0
_F = 1.0 / 298.257222101
_N = _F / (2.0 - _F)
_AA = _A / (1.0 + _N) * (1.0 + _N ** 2 / 4.0 + _N ** 4 / 64.0)
_ALFA = (
    _N / 2.0 - 2.0 * _N ** 2 / 3.0 + 5.0 * _N ** 3 / 16.0 + 41.0 * _N ** 4 / 180.0,
    13.0 * _N ** 2 / 48.0 - 3.0 * _N ** 3 / 5.0 + 557.0 * _N ** 4 / 1440.0,
    61.0 * _N ** 3 / 240.0 - 103.0 * _N ** 4 / 140.0,
    49561.0 * _N ** 4 / 161280.0,
)
_BETA = (
    _N / 2.0 - 2.0 * _N ** 2 / 3.0 + 37.0 * _N ** 3 / 96.0 - _N ** 4 / 360.0,
    _N ** 2 / 48.0 + _N ** 3 / 15.0 - 437.0 * _N ** 4 / 1440.0,
    17.0 * _N ** 3 / 480.0 - 37.0 * _N ** 4 / 840.0,
    4397.0 * _N ** 4 / 161280.0,
)
_DELTA = (
    2.0 * _N - 2.0 * _N ** 2 / 3.0 - 2.0 * _N ** 3 + 116.0 * _N ** 4 / 45.0,
    7.0 * _N ** 2 / 3.0 - 8.0 * _N ** 3 / 5.0 - 227.0 * _N ** 4 / 45.0,
    56.0 * _N ** 3 / 15.0 - 136.0 * _N ** 4 / 35.0,
    4279.0 * _N ** 4 / 630.0,
)

# TUREF / TM27 … TM45. Büyük ölçekli harita ve kadastro bu dilimlerde üretilir.
DOM_3 = (27, 30, 33, 36, 39, 42, 45)
EPSG_TM3 = {27: 5253, 30: 5254, 33: 5255, 36: 5256, 39: 5257, 42: 5258, 45: 5259}

SISTEMLER = {
    "tm3": {"k0": 1.0, "ad": "ITRF96 TM 3°"},
    "utm6": {"k0": 0.9996, "ad": "UTM 6°"},
}


def dom_sec(boylam: float, sistem: str = "tm3") -> int:
    """Boylama en yakın dilim orta meridyeni."""
    if sistem == "utm6":
        return int(math.floor((boylam + 180.0) / 6.0)) * 6 - 177
    dom = int(round((boylam - 27.0) / 3.0)) * 3 + 27
    return max(DOM_3[0], min(DOM_3[-1], dom))


def tm_ileri(enlem: float, boylam: float, dom: float, k0: float = 1.0,
             fe: float = 500000.0) -> Nokta:
    """Coğrafi (derece) → (sağa Y, yukarı X) metre."""
    fi = math.radians(enlem)
    dl = math.radians(boylam - dom)
    c = 2.0 * math.sqrt(_N) / (1.0 + _N)
    t = math.sinh(math.atanh(math.sin(fi)) - c * math.atanh(c * math.sin(fi)))
    xi = math.atan2(t, math.cos(dl))
    eta = math.atanh(math.sin(dl) / math.sqrt(1.0 + t * t))
    saga, yukari = eta, xi
    for j, a in enumerate(_ALFA, 1):
        saga += a * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
        yukari += a * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
    return fe + k0 * _AA * saga, k0 * _AA * yukari


def tm_geri(saga: float, yukari: float, dom: float, k0: float = 1.0,
            fe: float = 500000.0) -> Nokta:
    """(sağa Y, yukarı X) metre → (enlem, boylam) derece."""
    xi = yukari / (k0 * _AA)
    eta = (saga - fe) / (k0 * _AA)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, 1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    fi = chi
    for j, d in enumerate(_DELTA, 1):
        fi += d * math.sin(2 * j * chi)
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(fi), dom + math.degrees(lam)


# --------------------------------------------------------------------------- #
# Düzlem geometrisi — girdiler (Y, X) metre, halkalar AÇIK (son nokta ≠ ilk)   #
# --------------------------------------------------------------------------- #
def isaretli_alan(halka: Sequence[Nokta]) -> float:
    """Gauss alanı; saat yönünün tersine halka için pozitif."""
    s = 0.0
    n = len(halka)
    for i in range(n):
        x1, y1 = halka[i]
        x2, y2 = halka[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def cokgen_alani(halkalar: Sequence[Sequence[Nokta]]) -> float:
    """Dış halka eksi iç halkalar (parsel içindeki ada/boşluk)."""
    if not halkalar:
        return 0.0
    return abs(isaretli_alan(halkalar[0])) - sum(abs(isaretli_alan(h)) for h in halkalar[1:])


def cevre(halka: Sequence[Nokta]) -> float:
    n = len(halka)
    return sum(math.dist(halka[i], halka[(i + 1) % n]) for i in range(n))


def kenarlar(halka: Sequence[Nokta]) -> List[Dict[str, float]]:
    """Her kenarın boyu ve semti (grid kuzeyinden saat yönünde, grad)."""
    out = []
    n = len(halka)
    for i in range(n):
        (y1, x1), (y2, x2) = halka[i], halka[(i + 1) % n]
        semt = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 360.0
        out.append({"bas": i + 1, "son": (i + 1) % n + 1,
                    "boy": math.hypot(y2 - y1, x2 - x1), "semt_g": semt * 10.0 / 9.0})
    return out


def ic_acilar(halka: Sequence[Nokta]) -> List[float]:
    """Köşe iç açıları (grad). İçbükey köşede 200 gradı aşar."""
    n = len(halka)
    ccw = isaretli_alan(halka) > 0
    out = []
    for i in range(n):
        cx, cy = halka[i]
        px, py = halka[i - 1]
        nx, ny = halka[(i + 1) % n]
        a = (nx - cx, ny - cy)
        b = (px - cx, py - cy)
        if not ccw:
            a, b = b, a
        aci = math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])
        if aci < 0:
            aci += 2.0 * math.pi
        out.append(math.degrees(aci) * 10.0 / 9.0)
    return out


def agirlik_merkezi(halka: Sequence[Nokta]) -> Nokta:
    a = isaretli_alan(halka)
    if abs(a) < 1e-12:
        n = float(len(halka))
        return sum(p[0] for p in halka) / n, sum(p[1] for p in halka) / n
    cx = cy = 0.0
    n = len(halka)
    for i in range(n):
        x1, y1 = halka[i]
        x2, y2 = halka[(i + 1) % n]
        k = x1 * y2 - x2 * y1
        cx += (x1 + x2) * k
        cy += (y1 + y2) * k
    return cx / (6.0 * a), cy / (6.0 * a)


def sinir_kutusu(noktalar: Sequence[Nokta]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in noktalar]
    ys = [p[1] for p in noktalar]
    return min(xs), min(ys), max(xs), max(ys)


def nokta_icinde(p: Nokta, halka: Sequence[Nokta]) -> bool:
    x, y = p
    ic = False
    n = len(halka)
    for i in range(n):
        x1, y1 = halka[i]
        x2, y2 = halka[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            ic = not ic
    return ic


def cokgen_icinde(p: Nokta, halkalar: Sequence[Sequence[Nokta]]) -> bool:
    if not halkalar or not nokta_icinde(p, halkalar[0]):
        return False
    return not any(nokta_icinde(p, h) for h in halkalar[1:])


def _dogruya_uzaklik(p: Nokta, a: Nokta, b: Nokta) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    if d2 == 0.0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def sinira_uzaklik(p: Nokta, halkalar: Sequence[Sequence[Nokta]]) -> float:
    en = float("inf")
    for h in halkalar:
        n = len(h)
        for i in range(n):
            en = min(en, _dogruya_uzaklik(p, h[i], h[(i + 1) % n]))
    return en


def etiket_noktasi(halkalar: Sequence[Sequence[Nokta]], izgara: int = 24) -> Tuple[Nokta, float]:
    """Sınıra en uzak iç nokta ve o uzaklık (yaklaşık "erişilmezlik kutbu").

    Ağırlık merkezi L ve U biçimli parsellerde parselin dışına düşer; etiketi
    oraya koymak komşu parseli etiketlemek olur. İki turlu ızgara araması kroki
    için fazlasıyla yeterli ve belirlenimcidir.
    """
    x0, y0, x1, y1 = sinir_kutusu(halkalar[0])
    en_iyi: Optional[Nokta] = None
    en_d = -1.0
    for _ in range(2):
        dx, dy = (x1 - x0) / izgara, (y1 - y0) / izgara
        for i in range(izgara + 1):
            for j in range(izgara + 1):
                p = (x0 + i * dx, y0 + j * dy)
                if cokgen_icinde(p, halkalar):
                    d = sinira_uzaklik(p, halkalar)
                    if d > en_d:
                        en_iyi, en_d = p, d
        if en_iyi is None:
            break
        x0, x1 = en_iyi[0] - dx, en_iyi[0] + dx
        y0, y1 = en_iyi[1] - dy, en_iyi[1] + dy
    if en_iyi is None:
        return agirlik_merkezi(halkalar[0]), 0.0
    return en_iyi, en_d
