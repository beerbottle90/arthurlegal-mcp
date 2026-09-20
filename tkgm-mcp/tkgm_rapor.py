"""tkgm_rapor — parselden teslim edilebilir belge: parsel föyü (.docx) ve portföy (.xlsx).

Dosyaya giren şey SVG ya da JSON değildir; Word ve Excel'dir. OOXML bir zip içinde
birkaç XML olduğu için burada elle yazılır: `pip install` yok, bağımlılık yok.

Föydeki görsel iki kez üretilir. Word SVG'yi ancak bir PNG yedeğiyle birlikte kabul
eder ve 2016 (MSI) ile öncesi yalnız o yedeği gösterir; bu yüzden aynı yerleşimden
(`_yerlesim`) hem vektör hem raster çıkar. Raster için de kütüphane yoktur: çokgen
tarama satırlarıyla doldurulur, PNG zlib + struct ile yazılır. Yedekte yazı yoktur;
tek istisna köşe numaralarıdır (yedi parçalı rakam), çünkü kenar tablosu onlara
atıf yapar ve numarasız bir çizimde "1-2" kenarının hangisi olduğu bilinemez.

Görsel A4 kroki sayfasının küçültülmüşü değildir: yalnız parsel çizimidir, yuvarlak
bir ölçekte ve milimetre ölçüsüyle yerleşir. Belge %100 ölçekle yazdırıldığında
başlıktaki ölçekte çıkar; `tkgm_kroki` ile aynı söz.

Bu modül ağa çıkmaz. Portföydeki Parsel Sorgu sütunu yalnız bir adres dizgisidir;
kullanıcı onu kendi tarayıcısında açar (Kullanım Koşulları md. 3).
"""

from __future__ import annotations

import io
import math
import re
import struct
import zipfile
import zlib
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

import tkgm_geo as geo
import tkgm_hukuk as hukuk
from tkgm_analiz import izdusur, olc, tr_bicim
from tkgm_kroki import OLCEKLER, UYARI
from tkgm_parsel import etiket

Nokta = Tuple[float, float]
Renk = Tuple[int, int, int]

PARSEL_SORGU = "https://parselsorgu.tkgm.gov.tr/"

_AZAMI_GEN, _AZAMI_YUK = 170.0, 110.0   # görselin sayfada kaplayabileceği alan (mm)
_ASGARI = 60.0                          # küçücük parselde pul kadar görsel çıkmasın
# Parselin çevresinde bırakılan pay (mm). Kenar yazısı 4, köşe numarası 5 mm dışarı taşar;
# komşu varsa etiketi bu şeride sığmalı ve o yazılarla çakışmamalıdır.
_PAY, _PAY_KOMSULU = 12.0, 16.0
_UZUN_KENAR_PX = 1400
_ORNEK = 3                              # kenar yumuşatma: piksel başına 3×3 alt örnek
_ETIKET_SINIRI = 60                     # kroki ile aynı: bundan çok köşede yazı okunmaz
_KENAR_SINIRI = 40
_CUBUKLAR = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)

_BEYAZ: Renk = (255, 255, 255)
_SIYAH: Renk = (0x11, 0x11, 0x11)
_DOLGU: Renk = (0xFF, 0xF3, 0xD6)
_KOMSU_DOLGU: Renk = (0xF4, 0xF4, 0xF4)
_KOMSU_CIZGI: Renk = (0x8A, 0x8A, 0x8A)
_KOSE: Renk = (0xB3, 0x26, 0x1E)

# Yedi parçalı rakam: a üst, b sağ üst, c sağ alt, d alt, e sol alt, f sol üst, g orta.
# Izgara 1 birim eninde, 2 birim boyundadır. "1" hücrenin sağına (b, c) çizilirse "210"
# "2 10" diye okunur; bu yüzden kendi dar hücresinin ortasında tek çizgidir (h).
_PARCA = {"a": ((0, 0), (1, 0)), "b": ((1, 0), (1, 1)), "c": ((1, 1), (1, 2)),
          "d": ((0, 2), (1, 2)), "e": ((0, 1), (0, 2)), "f": ((0, 0), (0, 1)),
          "g": ((0, 1), (1, 1)), "h": ((0.5, 0), (0.5, 2)), "/": ((0, 2), (1, 0))}
_RAKAM = {"0": "abcdef", "1": "h", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
          "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abfgcd", "/": "/", "-": "g"}
_DAR = {"1": 0.3}

# Zip girdilerinin zaman damgası sabittir: çıktı saatin değil girdinin işlevi olsun
# (test edilebilirlik, önbellek, fark alma). Tarih belgeye `tarih` ile girer.
_ZAMAN = (1980, 1, 1, 0, 0, 0)
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
# XML 1.0'ın kabul etmediği denetim karakterleri. Kullanıcı dosyasından gelen tek bir
# \x0b, Word'ün belgeyi "bozuk" diye reddetmesine yeter.
_GECERSIZ = re.compile("[^\t\n\r\x20-\U0000D7FF\U0000E000-\U0000FFFD\U00010000-\U0010FFFF]")

_NS_PAKET = "http://schemas.openxmlformats.org/package/2006"
_NS_BELGE = "http://schemas.openxmlformats.org/officeDocument/2006"
_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006"
_NS_SVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
_TUR_W = "application/vnd.openxmlformats-officedocument.wordprocessingml."
_TUR_S = "application/vnd.openxmlformats-officedocument.spreadsheetml."

_SAYFA_GEN = 9638        # A4 − 2×2 cm kenar = 17 cm, twip (1/1440 inç)
_KENAR_BOSLUGU = 1134    # 2 cm


def _x(deger: Any) -> str:
    return escape(_GECERSIZ.sub("", str(deger)), {'"': "&quot;"})


def _f(x: float) -> str:
    return ("%.2f" % x).rstrip("0").rstrip(".")


def _isaretli(x: float, hane: int = 2) -> str:
    return ("+" if x >= 0 else "") + tr_bicim(x, hane)


# --------------------------------------------------------------------------- #
# Yerleşim — SVG ile PNG aynı milimetre koordinatlarından çizilir               #
# --------------------------------------------------------------------------- #
def _kirp(halka: Sequence[Nokta], gen: float, yuk: float) -> List[Nokta]:
    """Halkayı [0, gen] × [0, yuk] çerçevesine kırpar (Sutherland–Hodgman).

    Komşu parsel çerçeveden taşar; yol ya da dere parseli kilometrelerce sürebilir.
    `clipPath`e bırakılmadı: Office'in SVG işleyicisinin onu nasıl ele aldığı burada
    sınanamıyor, raster tarafı da aynı noktaları kullanıyor. Kırpmanın çerçeve
    boyunca bıraktığı kenarlar en son çizilen çerçeve çizgisinin altında kalır.
    """
    noktalar = list(halka)
    for eksen, sinir, yon in ((0, 0.0, 1.0), (0, gen, -1.0), (1, 0.0, 1.0), (1, yuk, -1.0)):
        if not noktalar:
            break
        yeni: List[Nokta] = []
        onceki = noktalar[-1]
        for p in noktalar:
            p_icte = (p[eksen] - sinir) * yon >= 0.0
            if p_icte != ((onceki[eksen] - sinir) * yon >= 0.0):
                t = (sinir - onceki[eksen]) / (p[eksen] - onceki[eksen])
                yeni.append((onceki[0] + t * (p[0] - onceki[0]), onceki[1] + t * (p[1] - onceki[1])))
            if p_icte:
                yeni.append(p)
            onceki = p
        noktalar = yeni
    return noktalar


def _yerlesim(parsel: Dict[str, Any], komsular: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Ölçeği seçer ve çizilecek her şeyi kâğıt milimetresine çevirir.

    Çerçeve parsele göre büyür: ölçek önce seçilir, görsel o ölçekte parselin
    kapladığı yer + pay kadardır. Sabit bir kare çerçeve, uzun ince bir parselde
    sayfanın yarısını boşa harcardı.
    """
    olcu = olc(parsel)
    dom, tm = olcu["dom"], olcu["tm"]
    # `tkgm_kroki` ile aynı kural: dosya koordinatları kaba yuvarlanmışsa (Parsel Sorgu dışa
    # aktarımı ~1 m) santimetre yazmak sahte kesinliktir; boylar "≈" ve tek ondalıkla yazılır.
    kaba = "kenar_belirsizligi_m" in olcu
    y0, x0, y1, x1 = geo.sinir_kutusu([p for c in tm for p in c[0]])
    my, mx = (y0 + y1) / 2.0, (x0 + x1) / 2.0
    pay = _PAY_KOMSULU if komsular else _PAY
    olcek = next((m for m in OLCEKLER
                  if (y1 - y0) * 1000.0 / m <= _AZAMI_GEN - 2.0 * pay
                  and (x1 - x0) * 1000.0 / m <= _AZAMI_YUK - 2.0 * pay), OLCEKLER[-1])
    gen = min(_AZAMI_GEN, max(_ASGARI, math.ceil((y1 - y0) * 1000.0 / olcek + 2.0 * pay)))
    yuk = min(_AZAMI_YUK, max(_ASGARI, math.ceil((x1 - x0) * 1000.0 / olcek + 2.0 * pay)))

    def kagit(p: Nokta) -> Nokta:
        return (gen / 2.0 + (p[0] - my) * 1000.0 / olcek, yuk / 2.0 - (p[1] - mx) * 1000.0 / olcek)

    kc = [[[kagit(p) for p in h] for h in c] for c in tm]

    # Kenar boyları ve köşe numaraları: numaralama `tkgm_kroki.ciz` ve `geometri`
    # aracıyla aynı sırayı izler (parça parça dış halkalar), yoksa tablo ile çizim ayrışır.
    kenar_yazilari: List[Tuple[float, float, float, str]] = []
    kose_yazilari: List[Tuple[Nokta, Optional[Nokta], str]] = []
    yerlesen: List[Nokta] = []
    if olcu["kose_sayisi"] <= _ETIKET_SINIRI:
        no = 0
        for c_tm, c_k in zip(tm, kc):
            dis_tm, dis_k = c_tm[0], c_k[0]
            n = len(dis_k)
            normaller: List[Nokta] = []
            for i in range(n):
                (ax, ay), (bx, by) = dis_k[i], dis_k[(i + 1) % n]
                boy_mm = math.hypot(bx - ax, by - ay) or 1e-9
                nx, ny = (by - ay) / boy_mm, -(bx - ax) / boy_mm
                orta = ((ax + bx) / 2.0, (ay + by) / 2.0)
                if geo.cokgen_icinde((orta[0] + nx * 0.2, orta[1] + ny * 0.2), c_k):
                    nx, ny = -nx, -ny
                normaller.append((nx, ny))
                if boy_mm >= 9.0:
                    aci = math.degrees(math.atan2(by - ay, bx - ax))
                    if aci > 90.0:
                        aci -= 180.0
                    elif aci <= -90.0:
                        aci += 180.0
                    boy_m = math.dist(dis_tm[i], dis_tm[(i + 1) % n])
                    kenar_yazilari.append((orta[0] + nx * 2.8, orta[1] + ny * 2.8, aci,
                                           "≈" + tr_bicim(boy_m, 1) if kaba else tr_bicim(boy_m)))
            for i in range(n):
                no += 1
                (n1x, n1y), (n2x, n2y) = normaller[i - 1], normaller[i]
                bx, by = n1x + n2x, n1y + n2y
                b = math.hypot(bx, by)
                bx, by = (bx / b, by / b) if b > 1e-6 else (n2x, n2y)
                px, py = dis_k[i]
                # Sık köşelerde numaralar üst üste biner ve hiçbiri okunmaz. Çakışan numara
                # atlanır, köşe noktası kalır: 22 ile 24 arasındaki noktanın 23 olduğu sıradan bellidir.
                yazi: Optional[Nokta] = (px + bx * 3.2, py + by * 3.2)
                if any(abs(yazi[0] - q[0]) < 3.4 and abs(yazi[1] - q[1]) < 2.6 for q in yerlesen):
                    yazi = None
                else:
                    yerlesen.append(yazi)
                kose_yazilari.append(((px, py), yazi, str(no)))

    komsu_cizimleri: List[Dict[str, Any]] = []
    for k in komsular:
        _, ktm = izdusur(k, dom)     # ana parselin dilimi: dilim sınırındaki komşu kaymasın
        cokgenler = []
        for c in ktm:
            halkalar = [_kirp([kagit(p) for p in h], gen, yuk) for h in c]
            if len(halkalar[0]) >= 3:
                cokgenler.append([halkalar[0]] + [h for h in halkalar[1:] if len(h) >= 3])
        if not cokgenler:
            continue
        kayit: Dict[str, Any] = {"halkalar": [h for c in cokgenler for h in c]}
        (ex, ey), yaricap = geo.etiket_noktasi(cokgenler[0])
        if yaricap > 2.0:
            oz = k["oznitelik"]
            kayit["etiket"] = (ex, ey, "%s/%s" % (oz.get("ada") or "?", oz.get("parsel") or "?"))
        komsu_cizimleri.append(kayit)

    oz = parsel["oznitelik"]
    (ex, ey), yaricap = geo.etiket_noktasi(kc[0])
    cubuk_m = max((c for c in _CUBUKLAR if c * 1000.0 / olcek <= 30.0), default=_CUBUKLAR[0])
    # Dosya kayıtlı alanı taşıyorsa etikette o yazar (kroki ile aynı): geometriden hesaplanan
    # alan TKGM'nin kendi rakamının yerine konmaz; ikisi de ölçü tablosunda durur.
    alan_yazisi = ("%s m²" % tr_bicim(olcu["tapu_alani_m2"]) if "tapu_alani_m2" in olcu
                   else "%s%s m²" % ("≈" if kaba else "", tr_bicim(olcu["alan_m2"])))
    return {"olcu": olcu, "olcek": olcek, "gen": float(gen), "yuk": float(yuk), "kc": kc,
            "kaba": kaba, "alan_yazisi": alan_yazisi,
            "kenar_yazilari": kenar_yazilari, "kose_yazilari": kose_yazilari,
            "komsular": komsu_cizimleri,
            "etiket": (ex, ey, max(2.4, min(4.4, yaricap * 0.45)),
                       "%s/%s" % (oz.get("ada") or "?", oz.get("parsel") or "?")),
            "cubuk_m": cubuk_m, "cubuk_mm": cubuk_m * 1000.0 / olcek}


# Kuzey oku ve ölçek çubuğu payın DIŞ yarısında durur (çerçeveden en çok 6,5 mm içeride);
# kenar yazıları ve köşe numaraları ise İÇ yarısında (parselden en çok 6 mm dışarıda).
# İkisi bu yüzden çakışmaz. Çubuğun yazıları çubuğun üstünde değil yanındadır: üstte
# olsalardı çubuk 9 mm yükseklik tutar ve alt kenardaki köşe numaralarını örterdi.
def _kuzey_oku(gen: float) -> List[Nokta]:
    kx, ky = gen - 4.5, 2.5
    return [(kx, ky), (kx - 2.0, ky + 8.0), (kx, ky + 6.1), (kx + 2.0, ky + 8.0)]


def _cubuk_yeri(yer: Dict[str, Any]) -> Tuple[float, float, float]:
    """(çubuğun sol ucu, orta çizgisinin y'si, boyu) — mm."""
    return 5.0, yer["yuk"] - 3.4, yer["cubuk_mm"]


def _cubuk_zemini(yer: Dict[str, Any]) -> List[Nokta]:
    # Yalnız komşu çizgilerini örtmek içindir; parselden ve yazılardan ÖNCE çizilir.
    x0, y, boy = _cubuk_yeri(yer)
    return [(x0 - 3.6, y - 2.0), (x0 + boy + 13.0, y - 2.0), (x0 + boy + 13.0, y + 2.0), (x0 - 3.6, y + 2.0)]


# --------------------------------------------------------------------------- #
# SVG                                                                          #
# --------------------------------------------------------------------------- #
def _svg_yol(halkalar: Sequence[Sequence[Nokta]]) -> str:
    return " ".join("M" + " L".join("%s,%s" % (_f(x), _f(y)) for x, y in h) + " Z"
                    for h in halkalar)


def _svg_metin(x: float, y: float, s: str, boyut: float, capa: str = "middle", kalin: bool = False,
               renk: str = "#111", don: Optional[float] = None, hale: bool = True) -> str:
    """Beyaz haleli yazı — iki ayrı <text> ile.

    `tkgm_kroki` haleyi `paint-order` ile, dikey ortalamayı `dy="0.35em"` ile yapar;
    tarayıcıda doğrudur. `paint-order` SVG 2'dir ve Office'in işleyicisi SVG 1.1'e
    göre yazılmıştır: tanımazsa beyaz kontur harfin ÜSTÜNE biner ve yazı silinir.
    Burada hedef Word olduğu için hale ayrı bir öğedir, taban çizgisi de sayıyla kayar.
    """
    ek = ' font-weight="bold"' if kalin else ""
    if don is not None:
        ek += ' transform="rotate(%s %s %s)"' % (_f(don), _f(x), _f(y))
    govde = ('<text x="%s" y="%s" font-size="%s" text-anchor="%s"%s'
             % (_f(x), _f(y + boyut * 0.35), _f(boyut), capa, ek))
    out = ""
    if hale:
        out = ('%s fill="#fff" stroke="#fff" stroke-width="0.9" stroke-linejoin="round">%s</text>'
               % (govde, _x(s)))
    return out + '%s fill="%s">%s</text>' % (govde, renk, _x(s))


def _svg(yer: Dict[str, Any]) -> str:
    gen, yuk = yer["gen"], yer["yuk"]
    s: List[str] = []
    s.append('<svg xmlns="http://www.w3.org/2000/svg" width="%smm" height="%smm" '
             'viewBox="0 0 %s %s" font-family="Arial, Helvetica, sans-serif">'
             % (_f(gen), _f(yuk), _f(gen), _f(yuk)))
    s.append('<rect width="%s" height="%s" fill="#fff"/>' % (_f(gen), _f(yuk)))
    for k in yer["komsular"]:
        s.append('<path d="%s" fill="#f4f4f4" stroke="#8a8a8a" stroke-width="0.25" '
                 'fill-rule="evenodd"/>' % _svg_yol(k["halkalar"]))
    for k in yer["komsular"]:
        if "etiket" in k:
            s.append(_svg_metin(k["etiket"][0], k["etiket"][1], k["etiket"][2], 2.6, renk="#666"))
    if yer["komsular"]:
        s.append('<path d="%s" fill="#fff"/>' % _svg_yol([_cubuk_zemini(yer)]))
    s.append('<path d="%s" fill="#fff3d6" stroke="#111" stroke-width="0.6" '
             'stroke-linejoin="round" fill-rule="evenodd"/>'
             % _svg_yol([h for c in yer["kc"] for h in c]))
    for x, y, aci, metin in yer["kenar_yazilari"]:
        s.append(_svg_metin(x, y, metin, 2.6, don=aci))
    for (px, py), yazi_yeri, no in yer["kose_yazilari"]:
        s.append('<circle cx="%s" cy="%s" r="0.6" fill="#111"/>' % (_f(px), _f(py)))
        if yazi_yeri:
            s.append(_svg_metin(yazi_yeri[0], yazi_yeri[1], no, 2.4, renk="#b3261e"))
    ex, ey, punto, ap = yer["etiket"]
    s.append(_svg_metin(ex, ey - punto * 0.6, ap, punto, kalin=True))
    s.append(_svg_metin(ex, ey + punto * 0.7, yer["alan_yazisi"], punto * 0.72))

    ok = _kuzey_oku(gen)
    s.append('<path d="%s" fill="#111"/>' % _svg_yol([ok]))
    s.append(_svg_metin(ok[0][0] - 4.0, ok[0][1] + 2.0, "K", 3.0, kalin=True))
    bx0, by, cubuk = _cubuk_yeri(yer)
    s.append('<rect x="%s" y="%s" width="%s" height="1.2" fill="#111"/>'
             % (_f(bx0), _f(by - 0.6), _f(cubuk / 2.0)))
    s.append('<rect x="%s" y="%s" width="%s" height="1.2" fill="#fff" stroke="#111" '
             'stroke-width="0.2"/>' % (_f(bx0 + cubuk / 2.0), _f(by - 0.6), _f(cubuk / 2.0)))
    s.append(_svg_metin(bx0 - 1.0, by, "0", 2.4, "end", hale=False))
    s.append(_svg_metin(bx0 + cubuk + 1.0, by, "%s m" % tr_bicim(yer["cubuk_m"], 0), 2.4, "start",
                        hale=False))
    s.append('<rect x="0.18" y="0.18" width="%s" height="%s" fill="none" stroke="#111" '
             'stroke-width="0.35"/>' % (_f(gen - 0.36), _f(yuk - 0.36)))
    s.append("</svg>")
    return "\n".join(s)


# --------------------------------------------------------------------------- #
# PNG yedeği — tarama satırlı çokgen doldurma, zlib + struct ile yazım           #
# --------------------------------------------------------------------------- #
def _png_parca(tur: bytes, veri: bytes) -> bytes:
    return (struct.pack(">I", len(veri)) + tur + veri
            + struct.pack(">I", zlib.crc32(tur + veri) & 0xFFFFFFFF))


def _png(yer: Dict[str, Any]) -> bytes:
    """Yerleşimi ~1400 piksellik RGB PNG'ye çizer.

    Her şekil önce satır aralıklarına (x0, x1, renk) indirgenir; piksel piksel
    dolaşan saf Python döngüsü 1400² görselde saniyeler sürer, aralık ataması ise
    C hızındadır. Kenar yumuşatma 3×3 alt örnekledir ve ortalama da döngüsüz alınır:
    alt satırlar bayt dizisi olarak büyük tamsayıya çevrilip toplanır (her bayt
    ≤ 28 olduğundan dokuz örnek 255'i aşmaz, elde taşmaz), yatay toplam aynı sayının
    8 ve 16 bit kaydırılmışlarıyla, seyreltme dilimle, ölçekleme `translate` ile.
    """
    ppm = _UZUN_KENAR_PX / max(yer["gen"], yer["yuk"])            # piksel / mm
    gen, yuk = int(round(yer["gen"] * ppm)), int(round(yer["yuk"] * ppm))
    k = ppm * _ORNEK                                              # alt piksel / mm
    gen_alt, yuk_alt = gen * _ORNEK, yuk * _ORNEK
    satirlar: List[List[Tuple[int, int, Renk]]] = [[] for _ in range(yuk_alt)]

    def doldur(halkalar: Sequence[Sequence[Nokta]], renk: Renk) -> None:
        """Çift-tek kuralıyla doldurur; girdi milimetre, örnek noktası alt piksel merkezi."""
        kesisim: Dict[int, List[float]] = {}
        for h in halkalar:
            n = len(h)
            for i in range(n):
                x1, y1 = h[i][0] * k, h[i][1] * k
                x2, y2 = h[(i + 1) % n][0] * k, h[(i + 1) % n][1] * k
                if y1 == y2:
                    continue
                if y1 > y2:
                    x1, y1, x2, y2 = x2, y2, x1, y1
                egim = (x2 - x1) / (y2 - y1)
                # Yarı açık [y1, y2): ortak köşeden geçen satır iki kez sayılmaz.
                for j in range(max(0, int(math.ceil(y1 - 0.5))),
                               min(yuk_alt, int(math.ceil(y2 - 0.5)))):
                    kesisim.setdefault(j, []).append(x1 + (j + 0.5 - y1) * egim)
        for j, xs in kesisim.items():
            xs.sort()
            for a, b in zip(xs[::2], xs[1::2]):
                c0 = max(0, int(math.ceil(a - 0.5)))
                c1 = min(gen_alt, int(math.ceil(b - 0.5)))
                if c1 > c0:
                    satirlar[j].append((c0, c1, renk))

    def daire(m: Nokta, r: float, renk: Renk) -> None:
        doldur([[(m[0] + r * math.cos(i * math.pi / 8.0), m[1] + r * math.sin(i * math.pi / 8.0))
                 for i in range(16)]], renk)

    def cizgi(a: Nokta, b: Nokta, kalinlik: float, renk: Renk, yuvarlak: bool = True) -> None:
        # Her parça AYRI doldurulur: tek çağrıda verilseler çift-tek kuralı üst üste
        # binen yerleri (ekler, uçlar) boş bırakırdı.
        boy = math.hypot(b[0] - a[0], b[1] - a[1])
        if boy > 1e-9:
            nx, ny = -(b[1] - a[1]) / boy * kalinlik / 2.0, (b[0] - a[0]) / boy * kalinlik / 2.0
            doldur([[(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                     (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]], renk)
        if yuvarlak:
            daire(a, kalinlik / 2.0, renk)
            daire(b, kalinlik / 2.0, renk)

    def halka_ciz(h: Sequence[Nokta], kalinlik: float, renk: Renk, yuvarlak: bool) -> None:
        for i in range(len(h)):
            cizgi(h[i], h[(i + 1) % len(h)], kalinlik, renk, yuvarlak)

    def rakam(x: float, y: float, metin: str, boy: float, renk: Renk, capa: float = 0.5) -> None:
        """`capa`: x'in yazının neresi olduğu — 0 sol uç, 0.5 orta, 1 sağ uç."""
        ara, kalinlik = boy * 0.3, boy * 0.15
        enler = [boy * 0.5 * _DAR.get(ch, 1.0) for ch in metin]
        sol = x - (sum(enler) + (len(metin) - 1) * ara) * capa
        for r, kal in ((_BEYAZ, kalinlik + 0.8), (renk, kalinlik)):      # önce hale
            x0 = sol
            for ch, en in zip(metin, enler):
                for parca in _RAKAM[ch]:
                    (ax, ay), (bx, by) = _PARCA[parca]
                    cizgi((x0 + ax * en, y - boy / 2.0 + ay * boy / 2.0),
                          (x0 + bx * en, y - boy / 2.0 + by * boy / 2.0), kal, r)
                x0 += en + ara

    for kom in yer["komsular"]:
        doldur(kom["halkalar"], _KOMSU_DOLGU)
    for kom in yer["komsular"]:
        for h in kom["halkalar"]:
            halka_ciz(h, 0.25, _KOMSU_CIZGI, False)
    if yer["komsular"]:
        doldur([_cubuk_zemini(yer)], _BEYAZ)
    doldur([h for c in yer["kc"] for h in c], _DOLGU)
    for c in yer["kc"]:
        for h in c:
            halka_ciz(h, 0.6, _SIYAH, True)
    for kose, yazi_yeri, no in yer["kose_yazilari"]:
        daire(kose, 0.6, _SIYAH)
        if yazi_yeri:
            rakam(yazi_yeri[0], yazi_yeri[1], no, 2.4, _KOSE)
    ex, ey, punto, ap = yer["etiket"]
    if all(ch in _RAKAM for ch in ap):      # "123/4" yazılır, "?/4" ya da harfli ada yazılmaz
        rakam(ex, ey, ap, punto, _SIYAH)
    doldur([_kuzey_oku(yer["gen"])], _SIYAH)
    bx0, by, cubuk = _cubuk_yeri(yer)
    doldur([[(bx0, by - 0.6), (bx0 + cubuk / 2.0, by - 0.6), (bx0 + cubuk / 2.0, by + 0.6),
             (bx0, by + 0.6)]], _SIYAH)
    halka_ciz([(bx0 + cubuk / 2.0, by - 0.6), (bx0 + cubuk, by - 0.6), (bx0 + cubuk, by + 0.6),
               (bx0 + cubuk / 2.0, by + 0.6)], 0.2, _SIYAH, False)
    rakam(bx0 - 1.0, by, "0", 2.2, _SIYAH, 1.0)
    rakam(bx0 + cubuk + 1.0, by, str(yer["cubuk_m"]), 2.2, _SIYAH, 0.0)   # birim ("m") altyazıda
    halka_ciz([(0.18, 0.18), (yer["gen"] - 0.18, 0.18), (yer["gen"] - 0.18, yer["yuk"] - 0.18),
               (0.18, yer["yuk"] - 0.18)], 0.35, _SIYAH, False)

    # Birleştirme: alt satırlar → piksel satırı.
    kat = _ORNEK * _ORNEK
    azami = 255 // kat
    tablo = bytes(min(255, int(round(i * 255.0 / (azami * kat)))) for i in range(256))
    zemin = bytes([azami]) * gen_alt
    zemin_sayi = int.from_bytes(zemin, "little")
    bos_satir = b"\x00" + b"\xff" * (gen * 3)       # baştaki 0: PNG süzgeç türü "yok"
    ham = bytearray()
    for y in range(yuk):
        alt_satirlar = satirlar[y * _ORNEK:(y + 1) * _ORNEK]
        if not any(alt_satirlar):
            ham += bos_satir
            continue
        toplam = [0, 0, 0]
        for araliklar in alt_satirlar:
            for kanal in range(3):
                if not araliklar:
                    toplam[kanal] += zemin_sayi
                    continue
                sat = bytearray(zemin)
                for c0, c1, renk in araliklar:
                    sat[c0:c1] = bytes([int(round(renk[kanal] * azami / 255.0))]) * (c1 - c0)
                toplam[kanal] += int.from_bytes(sat, "little")
        satir = bytearray(gen * 3)
        for kanal in range(3):
            t = sum(toplam[kanal] >> (8 * i) for i in range(_ORNEK))
            satir[kanal::3] = t.to_bytes(gen_alt + _ORNEK, "little")[0:gen_alt:_ORNEK].translate(tablo)
        ham += b"\x00"
        ham += satir

    ppm_tam = int(round(ppm * 1000.0))
    return (b"\x89PNG\r\n\x1a\n"
            + _png_parca(b"IHDR", struct.pack(">IIBBBBB", gen, yuk, 8, 2, 0, 0, 0))
            + _png_parca(b"pHYs", struct.pack(">IIB", ppm_tam, ppm_tam, 1))
            + _png_parca(b"IDAT", zlib.compress(bytes(ham), 9))
            + _png_parca(b"IEND", b""))


# --------------------------------------------------------------------------- #
# Paket (OPC) ortak parçaları                                                   #
# --------------------------------------------------------------------------- #
def _zip(parcalar: Sequence[Tuple[str, Any]]) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        for ad, veri in parcalar:
            bilgi = zipfile.ZipInfo(ad, date_time=_ZAMAN)
            bilgi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(bilgi, veri.encode("utf-8") if isinstance(veri, str) else veri)
    return tampon.getvalue()


def _iliskiler(satirlar: Sequence[Tuple[str, str, str]]) -> str:
    """(kimlik, tür, hedef) → .rels. Tür `_NS_BELGE` altındaki son parçadır."""
    return (_XML + '<Relationships xmlns="%s/relationships">' % _NS_PAKET
            + "".join('<Relationship Id="%s" Type="%s/relationships/%s" Target="%s"/>'
                      % (kimlik, _NS_BELGE, tur, _x(hedef)) for kimlik, tur, hedef in satirlar)
            + "</Relationships>")


def _icerik_turleri(uzantilar: Sequence[Tuple[str, str]], parcalar: Sequence[Tuple[str, str]]) -> str:
    return (_XML + '<Types xmlns="%s/content-types">' % _NS_PAKET
            + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
              'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            + "".join('<Default Extension="%s" ContentType="%s"/>' % u for u in uzantilar)
            + "".join('<Override PartName="%s" ContentType="%s"/>' % p for p in parcalar)
            + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.'
              'openxmlformats-package.core-properties+xml"/></Types>')


def _kunye(baslik: str) -> str:
    return (_XML + '<cp:coreProperties xmlns:cp="%s/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>%s</dc:title>'
            "<dc:creator>ArthurLegal tkgm</dc:creator></cp:coreProperties>" % (_NS_PAKET, _x(baslik)))


def _kok_iliskiler(ana_parca: str) -> str:
    # core-properties ilişkisinin türü "package" ad alanındadır, ötekiler "officeDocument";
    # bu yüzden `_iliskiler` ile kurulamaz.
    return (_XML + '<Relationships xmlns="%s/relationships">'
            '<Relationship Id="rId1" Type="%s/relationships/officeDocument" Target="%s"/>'
            '<Relationship Id="rId2" Type="%s/relationships/metadata/core-properties" '
            'Target="docProps/core.xml"/></Relationships>' % (_NS_PAKET, _NS_BELGE, ana_parca, _NS_PAKET))


# --------------------------------------------------------------------------- #
# WordprocessingML                                                             #
# --------------------------------------------------------------------------- #
# Öğe sıraları şemadaki sıradır (pPr: keepNext → spacing → ind → jc; rPr: b → i →
# color → sz). Word sıra hatasını çoğu zaman sessizce onarır, "çoğu zaman" yetmez.
def _rpr(kalin: bool = False, boyut: int = 0, renk: str = "", italik: bool = False) -> str:
    oz = ("<w:b/>" if kalin else "") + ("<w:i/>" if italik else "")
    if renk:
        oz += '<w:color w:val="%s"/>' % renk
    if boyut:
        oz += '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (boyut, boyut)
    return "<w:rPr>%s</w:rPr>" % oz if oz else ""


def _run(metin: Any, kalin: bool = False, boyut: int = 0, renk: str = "", italik: bool = False) -> str:
    return ('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>'
            % (_rpr(kalin, boyut, renk, italik), _x(metin)))


def _par(icerik: str, stil: str = "", hiza: str = "", once: Optional[int] = None,
         sonra: Optional[int] = None, birlikte: bool = False, girinti: int = 0) -> str:
    oz = '<w:pStyle w:val="%s"/>' % stil if stil else ""
    if birlikte:
        oz += "<w:keepNext/>"
    if once is not None or sonra is not None:
        oz += "<w:spacing%s%s/>" % ('' if once is None else ' w:before="%d"' % once,
                                    '' if sonra is None else ' w:after="%d"' % sonra)
    if girinti:
        oz += '<w:ind w:left="%d" w:hanging="%d"/>' % (girinti, girinti)
    if hiza:
        oz += '<w:jc w:val="%s"/>' % hiza
    return "<w:p>%s%s</w:p>" % ("<w:pPr>%s</w:pPr>" % oz if oz else "", icerik)


def _hucre(metin: Any, gen: int, kalin: bool = False, dolgu: str = "", hiza: str = "",
           kapsam: int = 1) -> str:
    oz = '<w:tcW w:w="%d" w:type="dxa"/>' % gen
    if kapsam > 1:
        oz += '<w:gridSpan w:val="%d"/>' % kapsam
    if dolgu:
        oz += '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % dolgu
    # Aralık hücrede açıkça sıfırlanır: tablo stilinin paragraf ayarı Word'de belge
    # varsayılanını ezer ama LibreOffice ve Google Docs'ta ezmez.
    return "<w:tc><w:tcPr>%s</w:tcPr>%s</w:tc>" % (oz, _par(_run(metin, kalin), hiza=hiza, once=0, sonra=0))


def _tablo(satirlar: Sequence[str], sutunlar: Sequence[int]) -> str:
    return ('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="%d" w:type="dxa"/>'
            '<w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>%s</w:tblGrid>%s</w:tbl>'
            % (sum(sutunlar), "".join('<w:gridCol w:w="%d"/>' % g for g in sutunlar), "".join(satirlar)))


def _satir(hucreler: Sequence[str], baslik: bool = False) -> str:
    return "<w:tr><w:trPr><w:cantSplit/>%s</w:trPr>%s</w:tr>" % ("<w:tblHeader/>" if baslik else "",
                                                                  "".join(hucreler))


def _bilgi_tablosu(alanlar: Sequence[Tuple[str, str, bool]]) -> str:
    """(ad, değer, geniş) çiftlerini satır başına iki çift dizer; `geniş` olan satırı tek başına kaplar.

    Yirmi satırlık tek sütun tablo birinci sayfayı tek başına doldurur ve kroki
    ikinci sayfaya düşer; föyün işi ise krokiyi ölçülerle aynı sayfada göstermektir.
    """
    g = (1900, 2919, 1900, 2919)
    satirlar: List[str] = []
    bekleyen: List[Tuple[str, str]] = []

    def bosalt() -> None:
        if bekleyen:
            hucreler = []
            for i in range(2):
                ad, deger = bekleyen[i] if i < len(bekleyen) else ("", "")
                hucreler += [_hucre(ad, g[0], kalin=True, dolgu="F2F2F2" if ad else ""),
                             _hucre(deger, g[1])]
            satirlar.append(_satir(hucreler))
            del bekleyen[:]

    for ad, deger, genis in alanlar:
        if genis:
            bosalt()
            satirlar.append(_satir([_hucre(ad, g[0], kalin=True, dolgu="F2F2F2"),
                                    _hucre(deger, sum(g[1:]), kapsam=3)]))
        else:
            bekleyen.append((ad, deger))
            if len(bekleyen) == 2:
                bosalt()
    bosalt()
    return _tablo(satirlar, g)


def _kenar_tablosu(tm: Sequence[Sequence[Sequence[Nokta]]], kaba: bool) -> Tuple[str, int, int]:
    """(tablo XML'i, gösterilen, toplam). Kırk kenarı tek sütun dizmek bir sayfa tutar;
    kenarlar yan yana bloklara, blok içinde yukarıdan aşağı dizilir."""
    kenarlar: List[Tuple[str, str, str]] = []
    kayma = 0
    hane = 1 if kaba else 2
    for c in tm:
        for k in geo.kenarlar(c[0]):
            kenarlar.append(("%d-%d" % (k["bas"] + kayma, k["son"] + kayma), tr_bicim(k["boy"], hane),
                             tr_bicim(k["semt_g"], hane)))
        kayma += len(c[0])
    toplam = len(kenarlar)
    kenarlar = kenarlar[:_KENAR_SINIRI]
    blok = 1 if len(kenarlar) <= 10 else 2 if len(kenarlar) <= 20 else 3
    boy = int(math.ceil(len(kenarlar) / float(blok)))
    g = (900, 1156, 1156)
    satirlar = [_satir([_hucre(ad, gen, kalin=True, dolgu="F2F2F2", hiza="center")
                        for _ in range(blok)
                        for ad, gen in zip(("Kenar", "Boy (m)", "Semt (g)"), g)], baslik=True)]
    for i in range(boy):
        hucreler: List[str] = []
        for b in range(blok):
            j = b * boy + i
            kenar = kenarlar[j] if j < len(kenarlar) else ("", "", "")
            hucreler += [_hucre(kenar[0], g[0], hiza="center"), _hucre(kenar[1], g[1], hiza="right"),
                         _hucre(kenar[2], g[2], hiza="right")]
        satirlar.append(_satir(hucreler))
    return _tablo(satirlar, g * blok), len(kenarlar), toplam


def _gorsel(gen_mm: float, yuk_mm: float, aciklama: str) -> str:
    """PNG `a:blip`in kendisidir, SVG onun uzantısıdır: SVG bilmeyen Word uzantı
    listesini atlar ve PNG'yi gösterir, bilen Word SVG'yi çizer. Tersi kurulamaz."""
    cx, cy = int(round(gen_mm * 36000)), int(round(yuk_mm * 36000))      # 1 mm = 36000 EMU
    return ('<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            '<wp:extent cx="%d" cy="%d"/><wp:docPr id="1" name="Kroki" descr="%s"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="%s/picture"><pic:pic>'
            '<pic:nvPicPr><pic:cNvPr id="1" name="kroki.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="rIdPng"><a:extLst>'
            '<a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">'
            '<asvg:svgBlip xmlns:asvg="%s" r:embed="rIdSvg"/></a:ext></a:extLst></a:blip>'
            "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>"
            % (cx, cy, _x(aciklama), _NS_DRAWING, _NS_SVG, cx, cy))


def _hukuk_notlari(parsel: Dict[str, Any], konu: Optional[str]) -> str:
    nitelik = parsel["oznitelik"].get("nitelik", "")
    s: List[str] = []
    rejim = hukuk.nitelik_rejimleri(nitelik)
    if rejim:
        s.append(_par(_run("Nitelikten doğan rejim uyarıları (nitelik: %s)" % nitelik, kalin=True),
                      birlikte=True))
        for r in rejim:
            ek = "  [hukuk_koprusu konu=%s]" % r["konu"] if r.get("konu") else ""
            s.append(_par(_run("•  " + r["uyari"] + ek), girinti=284))
    else:
        # Sessizlik "kısıt yok" diye okunur; uyarının yokluğu yalnız sözcük eşleşmediğini gösterir.
        s.append(_par(_run("Nitelikten (%s) otomatik rejim uyarısı çıkmadı; bu, kısıt bulunmadığı "
                           "anlamına gelmez." % (nitelik or "—"))))
    if konu:
        k = hukuk.kopru(konu)
        s.append(_par(_run("Konu: %s" % k["baslik"], kalin=True), once=120, birlikte=True))
        s.append(_par(_run("Dayanak adresleri:"), birlikte=True))
        for d in k["dayanak"]:
            maddeler = ("md. " if d["maddeler"][:1].isdigit() else "") + d["maddeler"]
            s.append(_par(_run("•  %s sayılı %s — %s" % (d["mevzuat_no"], d["ad"], maddeler)),
                          girinti=284))
        for ad, anahtar in (("Uyarı", "uyari"), ("Süre", "sure")):
            if k.get(anahtar):
                s.append(_par(_run("%s: " % ad, kalin=True) + _run(k[anahtar])))
        s.append(_par(_run("Teyit: ", kalin=True) + _run(k["teyit"])))
    s.append(_par(_run(hukuk.DOGRULAMA, boyut=17, italik=True), once=120))
    return "".join(s)


def _belge(parsel: Dict[str, Any], yer: Dict[str, Any], konu: Optional[str], tarih: str) -> str:
    oz, olcu = parsel["oznitelik"], yer["olcu"]
    tapu = olcu.get("tapu_alani_m2")

    def v(anahtar: str) -> str:
        return str(oz.get(anahtar) or "—")

    oznitelikler: List[Tuple[str, str, bool]] = [
        ("İl", v("il"), False), ("İlçe", v("ilce"), False), ("Mahalle", v("mahalle"), False),
        ("Mevkii", v("mevkii"), False), ("Ada", v("ada"), False), ("Parsel", v("parsel"), False),
        ("Nitelik", v("nitelik"), False), ("Pafta", v("pafta"), False),
        ("Dosyadaki alan", "%s m²" % tr_bicim(tapu) if tapu else "—", False)]
    oznitelikler += [(ad, str(oz[a]), False) for ad, a in (("Zemin tipi", "zemin_tipi"), ("Durum", "durum"))
                     if oz.get(a)]
    oznitelikler += [(ad, str(oz[a]), True) for ad, a in (("Gittiği parseller", "gittigi_parseller"),
                                                          ("Gitme sebebi", "gittigi_sebep")) if oz.get(a)]
    kaba = yer["kaba"]
    olculer: List[Tuple[str, str, bool]] = [
        ("Hesap alanı", "%s%s m²" % ("≈" if kaba else "", tr_bicim(olcu["alan_m2"])), False),
        ("Dosyadaki alan", "%s m²" % tr_bicim(tapu) if tapu else "—", False),
        ("Fark", "%s m²" % _isaretli(olcu["fark_m2"]) if tapu else "—", False),
        ("Fark (%)", _isaretli(olcu["fark_yuzde"], 3) if tapu else "—", False),
        ("Çevre", "%s m" % tr_bicim(olcu["cevre_m"]), False),
        ("Köşe sayısı", "%d" % olcu["kose_sayisi"], False),
        ("Merkez enlem", tr_bicim(olcu["merkez"]["enlem"], 7), False),
        ("Merkez boylam", tr_bicim(olcu["merkez"]["boylam"], 7), False)]
    if olcu["parca_sayisi"] > 1 or olcu["ic_halka_sayisi"]:
        olculer += [("Parça sayısı", "%d" % olcu["parca_sayisi"], False),
                    ("İç halka sayısı", "%d" % olcu["ic_halka_sayisi"], False)]
    if kaba:
        olculer += [("Koordinat ızgarası", "~%s m" % tr_bicim(olcu["koordinat_adimi_m"], 1), False),
                    ("Alan belirsizliği", "±%s m² (%%95)" % tr_bicim(olcu["alan_belirsizligi_m2"], 1), False),
                    ("Kenar belirsizliği", "±%s m (%%95)" % tr_bicim(olcu["kenar_belirsizligi_m"], 2), False)]
    olculer.append(("Projeksiyon", olcu["projeksiyon"], True))
    # Farkın yorumu `olc`tan gelir: yuvarlama payının içinde mi dışında mı olduğunu o bilir.
    olcu_notu = "Hesap alanı projeksiyon düzleminde Gauss formülüyle bulunur."
    if olcu.get("hassasiyet_notu"):
        olcu_notu += " " + olcu["hassasiyet_notu"]
    if tapu:
        olcu_notu += " " + (olcu.get("fark_yorumu")
                            or "Dosyadaki alanla fark bir işarettir, hata tespiti değildir.")

    kenar_xml, gosterilen, toplam = _kenar_tablosu(olcu["tm"], kaba)
    altyazi = ("Ölçek 1/%s (belge %%100 ölçekle yazdırıldığında)  ·  ölçek çubuğu %s m  ·  "
               "yukarısı grid kuzeyi" % (tr_bicim(yer["olcek"], 0), tr_bicim(yer["cubuk_m"], 0)))
    if yer["komsular"]:
        altyazi += "  ·  gri: %d komşu parsel" % len(yer["komsular"])
    if not yer["kose_yazilari"]:
        altyazi += "  ·  köşe sayısı %d'ı aştığı için numara ve boy yazılmadı" % _ETIKET_SINIRI
    elif any(k[1] is None for k in yer["kose_yazilari"]):
        altyazi += "  ·  sık köşelerde çakışan numaralar atlandı (sıra kenar tablosunda)"
    kenar_notu = ("Kenar: baş-son köşe numarası (krokideki kırmızı numaralar); semt grid kuzeyinden "
                  "saat yönünde, grad.")
    if kaba:
        kenar_notu += ("  Dosya koordinatları ~%s m ızgaradadır: boylar ve semtler yaklaşıktır (kenar ±%s m, %%95)."
                       % (tr_bicim(olcu["koordinat_adimi_m"], 1), tr_bicim(olcu["kenar_belirsizligi_m"], 2)))
    if toplam > gosterilen:
        kenar_notu = ("İlk %d kenar gösterildi (toplam %d); tamamı için köşe listesi: disa_aktar "
                      "bicim=csv.  " % (gosterilen, toplam)) + kenar_notu

    s = [_par(_run("PARSEL FÖYÜ"), stil="Title"),
         _par(_run(etiket(parsel)), stil="Subtitle"),
         _par(_run("Öznitelikler (kullanıcının sağladığı dosyadan)"), stil="Heading1"),
         _bilgi_tablosu(oznitelikler),
         _par(_run("Ölçüler (hesap)"), stil="Heading1"),
         _bilgi_tablosu(olculer),
         _par(_run(olcu_notu, boyut=17, renk="555555"), once=60),
         _par(_run("Kroki"), stil="Heading1"),
         _par("<w:r>%s</w:r>" % _gorsel(yer["gen"], yer["yuk"], "Parsel krokisi: %s, ölçek 1/%d"
                                        % (etiket(parsel), yer["olcek"])),
              hiza="center", sonra=40, birlikte=True),
         _par(_run(altyazi, boyut=17, renk="555555"), hiza="center"),
         _par(_run("Kenar tablosu"), stil="Heading1"),
         kenar_xml,
         _par(_run(kenar_notu, boyut=17, renk="555555"), once=60),
         _par(_run("Hukuki notlar"), stil="Heading1"),
         _hukuk_notlari(parsel, konu)]
    s += [_par(_run(satir, boyut=17, renk="555555"), once=160 if i == 0 else 0, sonra=0)
          for i, satir in enumerate(UYARI)]
    if tarih:
        s.append(_par(_run("Düzenleme: %s — ArthurLegal tkgm" % tarih, boyut=17, renk="555555"), once=60))
    return (_XML + '<w:document xmlns:w="%s" xmlns:r="%s/relationships" '
            'xmlns:wp="%s/wordprocessingDrawing" xmlns:a="%s/main" xmlns:pic="%s/picture"><w:body>%s'
            '<w:sectPr><w:footerReference w:type="default" r:id="rIdAlt"/>'
            '<w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="%d" w:right="%d" w:bottom="%d" w:left="%d" w:header="567" w:footer="567" '
            'w:gutter="0"/></w:sectPr></w:body></w:document>'
            % (_NS_W, _NS_BELGE, _NS_DRAWING, _NS_DRAWING, _NS_DRAWING, "".join(s),
               _KENAR_BOSLUGU, _KENAR_BOSLUGU, _KENAR_BOSLUGU, _KENAR_BOSLUGU))


def _altbilgi() -> str:
    """Uyarının ilk satırı her sayfanın altındadır: föyün tek sayfası fotokopiyle
    dosyaya girdiğinde "resmî işlemde kullanılamaz" kaydı da onunla gitmelidir."""
    oz = _rpr(boyut=15, renk="777777")

    def alan(komut: str) -> str:
        # fldSimple değil karmaşık alan: Word alanı güncellerken sonucu alan kodunun İLK
        # karakterinin biçimiyle yazar; fldSimple'da o karakter yoktur ve sayfa numarası
        # altbilginin 7,5 puntosu yerine gövdenin 10 puntosuyla çıkar.
        return "".join("<w:r>%s%s</w:r>" % (oz, govde) for govde in (
            '<w:fldChar w:fldCharType="begin"/>',
            '<w:instrText xml:space="preserve"> %s </w:instrText>' % komut,
            '<w:fldChar w:fldCharType="separate"/>', "<w:t>1</w:t>", '<w:fldChar w:fldCharType="end"/>'))

    return (_XML + '<w:ftr xmlns:w="%s">%s</w:ftr>'
            % (_NS_W, _par(_run("%s  ·  Sayfa " % UYARI[0], boyut=15, renk="777777") + alan("PAGE")
                           + _run(" / ", boyut=15, renk="777777") + alan("NUMPAGES"),
                           hiza="center", sonra=0)))


def _stiller() -> str:
    def stil(kimlik: str, ad: str, ppr: str, rpr: str) -> str:
        return ('<w:style w:type="paragraph" w:styleId="%s"><w:name w:val="%s"/><w:basedOn w:val="Normal"/>'
                '<w:next w:val="Normal"/><w:qFormat/><w:pPr>%s</w:pPr><w:rPr>%s</w:rPr></w:style>'
                % (kimlik, ad, ppr, rpr))

    kenarlik = "".join('<w:%s w:val="single" w:sz="4" w:space="0" w:color="A6A6A6"/>' % k
                       for k in ("top", "left", "bottom", "right", "insideH", "insideV"))
    # Kimlikler Word'ün yerleşik adlarıdır (Title, heading 1, Table Grid): gezinti
    # bölmesi ve PDF yer imleri başlıkları bu adlardan tanır.
    return (_XML + '<w:styles xmlns:w="%s"><w:docDefaults><w:rPrDefault><w:rPr>'
            '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Arial"/>'
            '<w:sz w:val="20"/><w:szCs w:val="20"/><w:lang w:val="tr-TR" w:eastAsia="tr-TR" w:bidi="ar-SA"/>'
            '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="252" '
            'w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
            "<w:qFormat/></w:style>" % _NS_W
            + stil("Title", "Title", '<w:spacing w:after="0"/>',
                   '<w:b/><w:color w:val="1F3864"/><w:sz w:val="34"/><w:szCs w:val="34"/>')
            + stil("Subtitle", "Subtitle", '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" '
                   'w:color="1F3864"/></w:pBdr><w:spacing w:after="120"/>',
                   '<w:sz w:val="26"/><w:szCs w:val="26"/>')
            + stil("Heading1", "heading 1", '<w:keepNext/><w:spacing w:before="200" w:after="80"/>'
                   '<w:outlineLvl w:val="0"/>',
                   '<w:b/><w:color w:val="1F3864"/><w:sz w:val="23"/><w:szCs w:val="23"/>')
            + '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
              '<w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:tblPr><w:tblInd w:w="0" '
              'w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
              '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr>'
              "</w:style>"
              '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>'
              '<w:basedOn w:val="TableNormal"/><w:tblPr><w:tblBorders>%s</w:tblBorders><w:tblCellMar>'
              '<w:top w:w="30" w:type="dxa"/><w:left w:w="85" w:type="dxa"/><w:bottom w:w="30" '
              'w:type="dxa"/><w:right w:w="85" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
              "</w:styles>" % kenarlik)


# Uyumluluk kipi 15 yazılmazsa Word belgeyi 2007 belgesi sayar ve başlıkta
# "[Uyumluluk Modu]" gösterir; tablo stili ile belge varsayılanının önceliği de değişir.
_AYARLAR = (_XML + '<w:settings xmlns:w="%s"><w:compat><w:compatSetting w:name="compatibilityMode" '
            'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>' % _NS_W)


def foy_docx(parsel: Dict[str, Any], komsular: Optional[List[Dict[str, Any]]] = None,
             konu: Optional[str] = None, tarih: str = "") -> bytes:
    """Bir-iki sayfalık A4 "PARSEL FÖYÜ" (.docx baytları).

    `tarih` içeride üretilmez, çağıran verir: aynı girdi aynı baytları vermelidir.
    `konu` `tkgm_hukuk.KONULAR` anahtarıdır; bilinmeyen konu sessizce atlanmaz,
    çünkü föyde hukuki not bekleyen kullanıcı eksik çıktığını fark etmeyebilir.
    """
    if konu and konu not in hukuk.KONULAR:
        raise ValueError("Bilinmeyen konu '%s'. Geçerli: %s" % (konu, ", ".join(hukuk.KONULAR)))
    komsular = [k for k in (komsular or [])
                if k is not parsel and not (k.get("ref") and k.get("ref") == parsel.get("ref"))]
    yer = _yerlesim(parsel, komsular)
    return _zip([
        ("[Content_Types].xml", _icerik_turleri(
            (("png", "image/png"), ("svg", "image/svg+xml")),
            (("/word/document.xml", _TUR_W + "document.main+xml"),
             ("/word/styles.xml", _TUR_W + "styles+xml"),
             ("/word/settings.xml", _TUR_W + "settings+xml"),
             ("/word/footer1.xml", _TUR_W + "footer+xml")))),
        ("_rels/.rels", _kok_iliskiler("word/document.xml")),
        ("docProps/core.xml", _kunye("Parsel Föyü — %s" % etiket(parsel))),
        ("word/document.xml", _belge(parsel, yer, konu, tarih)),
        ("word/_rels/document.xml.rels", _iliskiler((
            ("rIdStil", "styles", "styles.xml"), ("rIdAyar", "settings", "settings.xml"),
            ("rIdPng", "image", "media/kroki.png"), ("rIdSvg", "image", "media/kroki.svg"),
            ("rIdAlt", "footer", "footer1.xml")))),
        ("word/styles.xml", _stiller()),
        ("word/settings.xml", _AYARLAR),
        ("word/footer1.xml", _altbilgi()),
        ("word/media/kroki.png", _png(yer)),
        ("word/media/kroki.svg", _svg(yer)),
    ])


# --------------------------------------------------------------------------- #
# SpreadsheetML                                                                #
# --------------------------------------------------------------------------- #
# Hücre stilleri (styles.xml içindeki cellXfs sırası).
_S_METIN, _S_BASLIK, _S_IKI, _S_UC, _S_TAM, _S_ALTI, _S_SARILI, _S_TOPLAM_AD, _S_TOPLAM = range(9)

# (başlık, genişlik, stil). Sıra görev tanımındaki sıradır; sütun eklemek için yalnız
# burası ve `_portfoy_satiri` değişir.
_SUTUNLAR = (
    ("İl", 14, _S_METIN), ("İlçe", 16, _S_METIN), ("Mahalle", 20, _S_METIN), ("Ada", 8, _S_TAM),
    ("Parsel", 8, _S_TAM), ("Nitelik", 22, _S_METIN), ("Mevkii", 16, _S_METIN), ("Pafta", 12, _S_METIN),
    ("Dosyadaki alan m²", 15, _S_IKI), ("Hesap alanı m²", 15, _S_IKI), ("Fark m²", 11, _S_IKI),
    ("Fark %", 10, _S_UC), ("Çevre m", 12, _S_IKI), ("Köşe", 7, _S_TAM),
    ("Merkez enlem", 13, _S_ALTI), ("Merkez boylam", 13, _S_ALTI), ("Rejim uyarıları", 60, _S_SARILI),
    ("Parsel Sorgu bağlantısı", 64, _S_METIN), ("ref", 15, _S_METIN),
    # Fark sütunlarının nasıl okunacağını bunlar söyler: koordinatları ~1 m'ye yuvarlanmış bir
    # dosyada birkaç m²'lik fark yuvarlamanın kendisidir, kadastro hatası değil.
    ("Alan belirsizliği ± m² (%95)", 14, _S_IKI), ("Fark yorumu", 60, _S_SARILI),
)
_ALAN_SUTUNLARI = (8, 9)     # toplamı alınan iki sütun: dosyadaki alan, hesap alanı


def _sutun_adi(i: int) -> str:
    ad = ""
    i += 1
    while i:
        i, kalan = divmod(i - 1, 26)
        ad = chr(65 + kalan) + ad
    return ad


def _tam_sayi(deger: Any) -> Any:
    """"123" → 123; "12/A" ya da "007" olduğu gibi kalır.

    Ada ve parsel dosyada dizgidir. Dizgi bırakılırsa Excel "10"u "4"ten önce
    sıralar; sayıya çevrilirken de baştaki sıfır gibi anlam taşıyan yazım kaybolmamalı.
    """
    s = str(deger or "").strip()
    return int(s) if re.fullmatch(r"0|[1-9]\d{0,8}", s) else s


def _hucre_xml(sutun: int, satir: int, deger: Any, stil: int) -> str:
    """Sayı sayı olarak (<v>), metin satır içi dizgi olarak yazılır. Boş değer hücre
    üretmez: olmayan bir tapu alanı 0 yazılırsa toplam doğru kalır ama satır yanlış okunur."""
    if deger is None or deger == "":
        return ""
    konum = "%s%d" % (_sutun_adi(sutun), satir)
    if isinstance(deger, (int, float)) and not isinstance(deger, bool):
        return '<c r="%s" s="%d"><v>%s</v></c>' % (konum, stil, repr(deger))
    if stil in (_S_IKI, _S_UC, _S_TAM, _S_ALTI):
        stil = _S_METIN      # sayı bekleyen sütuna metin düştü ("12/A"): sayı biçimi taşımasın
    return ('<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
            % (konum, stil, _x(deger)))


def _portfoy_satiri(parsel: Dict[str, Any]) -> List[Any]:
    oz, olcu = parsel["oznitelik"], olc(parsel)
    merkez = olcu["merkez"]
    return [oz.get("il"), oz.get("ilce"), oz.get("mahalle"), _tam_sayi(oz.get("ada")),
            _tam_sayi(oz.get("parsel")), oz.get("nitelik"), oz.get("mevkii"), oz.get("pafta"),
            olcu.get("tapu_alani_m2"), olcu["alan_m2"], olcu.get("fark_m2"), olcu.get("fark_yuzde"),
            olcu["cevre_m"], olcu["kose_sayisi"], merkez["enlem"], merkez["boylam"],
            "\n".join(r["uyari"] for r in hukuk.nitelik_rejimleri(oz.get("nitelik", ""))),
            # Düz metin, köprü değil: Excel köprüyü Address + SubAddress diye böler ve '#'
            # sonrası bazı Office yapılarında bozulur. Kopyalanan dizgi her yerde çalışır.
            "%s#ara/cografi/%.6f/%.6f" % (PARSEL_SORGU, merkez["enlem"], merkez["boylam"]),
            parsel.get("ref"), olcu.get("alan_belirsizligi_m2"), olcu.get("fark_yorumu")]


def _sayfa(satirlar: Sequence[str], sutun_genislikleri: Sequence[float], son_hucre: str,
           dondur: bool = False, suzgec: str = "", secili: bool = False) -> str:
    # Öğe sırası şemadaki sıradır (sheetPr, dimension, sheetViews, sheetFormatPr, cols,
    # sheetData, autoFilter, pageMargins, pageSetup); Excel sırası bozuk sayfayı "onarır".
    bolme = ('<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
             '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>' if dondur else "")
    return (_XML + '<worksheet xmlns="%s"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
            '<dimension ref="A1:%s"/><sheetViews><sheetView %sworkbookViewId="0">%s</sheetView>'
            '</sheetViews><sheetFormatPr defaultRowHeight="15"/><cols>%s</cols>'
            "<sheetData>%s</sheetData>%s"
            '<pageMargins left="0.4" right="0.4" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>'
            '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
            "</worksheet>"
            % (_NS_S, son_hucre, 'tabSelected="1" ' if secili else "", bolme,
               "".join('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, _f(g))
                       for i, g in enumerate(sutun_genislikleri)),
               "".join(satirlar), '<autoFilter ref="%s"/>' % suzgec if suzgec else ""))


def _xlsx_stiller() -> str:
    def xf(bicim: int, yazi: int = 0, dolgu: int = 0, kenarlik: int = 0, hizalama: str = "") -> str:
        uygula = "".join(' apply%s="1"' % ad for ad, var in (
            ("NumberFormat", bicim), ("Font", yazi), ("Fill", dolgu), ("Border", kenarlik),
            ("Alignment", hizalama)) if var)
        return ('<xf numFmtId="%d" fontId="%d" fillId="%d" borderId="%d" xfId="0"%s>%s</xf>'
                % (bicim, yazi, dolgu, kenarlik, uygula,
                   "<alignment %s/>" % hizalama if hizalama else ""))

    ust = 'vertical="top"'
    xfler = [xf(0, hizalama=ust),                                            # _S_METIN
             xf(0, 1, 2, hizalama='vertical="center" wrapText="1"'),         # _S_BASLIK
             xf(4, hizalama=ust),                                            # _S_IKI      #,##0.00
             xf(164, hizalama=ust),                                          # _S_UC       0.000
             xf(1, hizalama=ust),                                            # _S_TAM      0
             xf(165, hizalama=ust),                                          # _S_ALTI     0.000000
             xf(0, hizalama='vertical="top" wrapText="1"'),                  # _S_SARILI
             xf(0, 1, 0, 1),                                                 # _S_TOPLAM_AD
             xf(4, 1, 0, 1)]                                                 # _S_TOPLAM
    return (_XML + '<styleSheet xmlns="%s"><numFmts count="2"><numFmt numFmtId="164" formatCode="0.000"/>'
            '<numFmt numFmtId="165" formatCode="0.000000"/></numFmts>'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFE7E6E6"/><bgColor indexed="64"/>'
            "</patternFill></fill></fills>"
            '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
            '<border><left/><right/><top style="thin"><color auto="1"/></top><bottom/><diagonal/>'
            "</border></borders>"
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="%d">%s</cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            "</styleSheet>" % (_NS_S, len(xfler), "".join(xfler)))


def portfoy_xlsx(parseller: Sequence[Dict[str, Any]], tarih: str = "") -> bytes:
    """Parsel başına bir satırlık "Portföy" sayfası + "Notlar" (.xlsx baytları).

    Ölçüler gerçek sayı hücreleridir; Türkçe biçimli dizgi ("1.200,00") yazılsaydı
    Excel toplayamazdı. Binlik/ondalık gösterimi kullanıcının Excel yereli belirler.
    Toplam SUBTOTAL(109) ile alınır: süzgeç uygulandığında yalnız görünen parseller
    toplanır; SUM gizli satırları da sayar ve süzülmüş listede yanlış okunur.
    """
    n = len(parseller)
    son_sutun = _sutun_adi(len(_SUTUNLAR) - 1)
    satirlar = ['<row r="1" ht="30" customHeight="1">%s</row>'
                % "".join(_hucre_xml(i, 1, s[0], _S_BASLIK) for i, s in enumerate(_SUTUNLAR))]
    toplamlar = dict((i, 0.0) for i in _ALAN_SUTUNLARI)
    for sira, parsel in enumerate(parseller):
        degerler = _portfoy_satiri(parsel)
        for i in _ALAN_SUTUNLARI:
            toplamlar[i] += degerler[i] or 0.0
        satirlar.append('<row r="%d">%s</row>' % (sira + 2, "".join(
            _hucre_xml(i, sira + 2, d, _SUTUNLAR[i][2]) for i, d in enumerate(degerler))))
    if n:
        # Formülün yanında hesaplanmış değer de yazılır: yeniden hesaplamayan okuyucular
        # (önizleme, pandas, LibreOffice'in hızlı yüklemesi) boş hücre görmesin.
        hucreler = _hucre_xml(0, n + 2, "TOPLAM (%d parsel)" % n, _S_TOPLAM_AD)
        for i in range(1, len(_SUTUNLAR)):
            ad = _sutun_adi(i)
            if i in _ALAN_SUTUNLARI:
                hucreler += ('<c r="%s%d" s="%d"><f>SUBTOTAL(109,%s2:%s%d)</f><v>%s</v></c>'
                             % (ad, n + 2, _S_TOPLAM, ad, ad, n + 1, repr(round(toplamlar[i], 2))))
            else:
                hucreler += '<c r="%s%d" s="%d"/>' % (ad, n + 2, _S_TOPLAM_AD)
        satirlar.append('<row r="%d">%s</row>' % (n + 2, hucreler))

    notlar = ["PARSEL PORTFÖYÜ — NOTLAR"]
    if tarih:
        notlar.append("Düzenleme: %s — ArthurLegal tkgm" % tarih)
    notlar += list(UYARI) + [
        "Hesap alanı ITRF96 TM 3° düzleminde Gauss formülüyle bulunur. Dosyadaki alanla fark bir "
        "işarettir, hata tespiti değildir.",
        "Rejim uyarıları parsel niteliğindeki sözcüklerden türetilir; uyarı çıkmaması kısıt "
        "bulunmadığı anlamına gelmez.",
        "Parsel Sorgu bağlantısını kullanıcı kendi tarayıcısında açar; bu araç TKGM'ye istek "
        "göndermez (Kullanım Koşulları md. 3). Bağlantı bilerek düz metindir: kopyalayıp adres "
        "çubuğuna yapıştırın.",
        "Toplam satırı süzgece duyarlıdır (SUBTOTAL 109): yalnız görünen parseller toplanır.",
        hukuk.DOGRULAMA]
    not_satirlari = ['<row r="%d">%s</row>' % (i + 1, _hucre_xml(0, i + 1, metin,
                                                                 _S_BASLIK if i == 0 else _S_SARILI))
                     for i, metin in enumerate(notlar)]

    return _zip([
        ("[Content_Types].xml", _icerik_turleri((), (
            ("/xl/workbook.xml", _TUR_S + "sheet.main+xml"),
            ("/xl/worksheets/sheet1.xml", _TUR_S + "worksheet+xml"),
            ("/xl/worksheets/sheet2.xml", _TUR_S + "worksheet+xml"),
            ("/xl/styles.xml", _TUR_S + "styles+xml")))),
        ("_rels/.rels", _kok_iliskiler("xl/workbook.xml")),
        ("docProps/core.xml", _kunye("Parsel Portföyü (%d parsel)" % n)),
        ("xl/workbook.xml",
         _XML + '<workbook xmlns="%s" xmlns:r="%s/relationships"><bookViews><workbookView/></bookViews>'
         '<sheets><sheet name="Portföy" sheetId="1" r:id="rId1"/>'
         '<sheet name="Notlar" sheetId="2" r:id="rId2"/></sheets></workbook>' % (_NS_S, _NS_BELGE)),
        ("xl/_rels/workbook.xml.rels", _iliskiler((
            ("rId1", "worksheet", "worksheets/sheet1.xml"), ("rId2", "worksheet", "worksheets/sheet2.xml"),
            ("rId3", "styles", "styles.xml")))),
        ("xl/styles.xml", _xlsx_stiller()),
        ("xl/worksheets/sheet1.xml", _sayfa(
            satirlar, [s[1] for s in _SUTUNLAR], "%s%d" % (son_sutun, max(1, n + 2 if n else 1)),
            dondur=True, suzgec="A1:%s%d" % (son_sutun, n + 1) if n else "", secili=True)),
        ("xl/worksheets/sheet2.xml", _sayfa(not_satirlari, [120], "A%d" % len(notlar))),
    ])
