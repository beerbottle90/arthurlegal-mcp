"""tkgm_kroki — ölçekli SVG parsel krokisi (A4, milimetre birimli).

SVG'nin kullanıcı birimi milimetredir ve sayfa 210×297'dir: dosya %100 ölçekle
yazdırıldığında çizim başlıktaki ölçekte çıkar. Piksel tabanlı bir görselde
"1/500" yazmak süstür; burada ölçüdür.

Çizim metin olarak üretilir, raster yoktur: dört köşeli bir parsel ~4 KB tutar.
Araç bunu dosyaya yazar ve modele yalnız yolu döndürür.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

import tkgm_geo as geo
from tkgm_analiz import izdusur, olc, tr_bicim
from tkgm_parsel import etiket

Nokta = Tuple[float, float]

OLCEKLER = (50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 25000,
            50000, 100000, 250000, 500000)
_CUBUKLAR = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)

_FX, _FY, _FW, _FH = 20.0, 22.0, 170.0, 170.0   # çizim çerçevesi
_ETIKET_SINIRI = 60      # bundan çok köşede kenar/köşe yazısı okunmaz; tabloya bırakılır
_TABLO_SINIRI = 42       # 3 sütun × 14 satır

UYARI = ("Bilgi amaçlıdır; resmî işlemlerde kullanılamaz (TKGM Parsel Sorgu Kullanım Koşulları md. 4).",
         "Geometri kullanıcının sağladığı dosyadan alınmıştır; aplikasyon krokisi veya röperli "
         "kroki yerine geçmez.")


def _f(x: float) -> str:
    return ("%.2f" % x).rstrip("0").rstrip(".")


def _yol(halkalar: Sequence[Sequence[Nokta]]) -> str:
    return " ".join("M" + " L".join("%s,%s" % (_f(x), _f(y)) for x, y in h) + " Z"
                    for h in halkalar)


def _metin(x, y, s, boyut=3.0, capa="middle", kalin=False, renk="#111", don=None, hale=True):
    ek = ' font-weight="bold"' if kalin else ""
    if don is not None:
        ek += ' transform="rotate(%s %s %s)"' % (_f(don), _f(x), _f(y))
    if hale:
        ek += ' paint-order="stroke" stroke="#fff" stroke-width="0.9"'
    return ('<text x="%s" y="%s" font-size="%s" text-anchor="%s" fill="%s" dy="0.35em"%s>%s</text>'
            % (_f(x), _f(y), _f(boyut), capa, renk, ek, escape(s)))


def ciz(parsel: Dict[str, Any], komsular: Optional[List[Dict[str, Any]]] = None,
        koordinat_tablosu: bool = False, tarih: str = "") -> Tuple[str, Dict[str, Any]]:
    """(svg metni, özet) döndürür."""
    olcu = olc(parsel)
    dom, tm = olcu["dom"], olcu["tm"]
    komsular = komsular or []
    kaba = "kenar_belirsizligi_m" in olcu

    disaridakiler = [p for c in tm for p in c[0]]
    y0, x0, y1, x1 = geo.sinir_kutusu(disaridakiler)
    my, mx = (y0 + y1) / 2.0, (x0 + x1) / 2.0
    # Komşu varsa parsel çerçevenin ~%60'ını kaplar ki çevresi görünsün.
    kullanilir = 100.0 if komsular else 138.0
    genislik = max(y1 - y0, x1 - x0, 0.01)
    olcek = next((m for m in OLCEKLER if genislik * 1000.0 / m <= kullanilir), OLCEKLER[-1])

    def kagit(p: Nokta) -> Nokta:
        return (_FX + _FW / 2.0 + (p[0] - my) * 1000.0 / olcek,
                _FY + _FH / 2.0 - (p[1] - mx) * 1000.0 / olcek)

    kc = [[[kagit(p) for p in h] for h in c] for c in tm]   # kâğıt koordinatları

    s: List[str] = []
    s.append('<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" '
             'viewBox="0 0 210 297" font-family="Arial, Helvetica, sans-serif">')
    s.append('<rect width="210" height="297" fill="#fff"/>')
    s.append('<clipPath id="c"><rect x="%s" y="%s" width="%s" height="%s"/></clipPath>'
             % (_f(_FX), _f(_FY), _f(_FW), _f(_FH)))
    s.append(_metin(_FX, 14, "PARSEL KROKİSİ", 5.0, "start", True, hale=False))
    s.append(_metin(_FX + _FW, 14, "Ölçek 1/%s" % tr_bicim(olcek, 0), 4.0, "end", hale=False))

    s.append('<g clip-path="url(#c)">')
    for k in komsular:
        _, ktm = izdusur(k, dom)
        kk = [[[kagit(p) for p in h] for h in c] for c in ktm]
        s.append('<path d="%s" fill="#f4f4f4" stroke="#8a8a8a" stroke-width="0.25" '
                 'fill-rule="evenodd"/>' % _yol([h for c in kk for h in c]))
        (ex, ey), yaricap = geo.etiket_noktasi(kk[0])
        if _FX < ex < _FX + _FW and _FY < ey < _FY + _FH and yaricap > 2.0:
            oz = k["oznitelik"]
            s.append(_metin(ex, ey, "%s/%s" % (oz.get("ada") or "?", oz.get("parsel") or "?"),
                            2.6, renk="#666"))
    s.append('<path d="%s" fill="#fff3d6" stroke="#111" stroke-width="0.5" '
             'stroke-linejoin="round" fill-rule="evenodd"/>' % _yol([h for c in kc for h in c]))
    s.append("</g>")

    # Kenar boyları ve köşe numaraları
    kose_toplam = olcu["kose_sayisi"]
    etiketli = atlanan = 0
    if kose_toplam <= _ETIKET_SINIRI:
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
                    # Dosya koordinatları kabaysa santimetre yazmak sahte kesinliktir.
                    boy_yazi = "≈" + tr_bicim(boy_m, 1) if kaba else tr_bicim(boy_m)
                    s.append(_metin(orta[0] + nx * 2.8, orta[1] + ny * 2.8, boy_yazi, 2.6, don=aci))
                    etiketli += 1
                else:
                    atlanan += 1
            for i in range(n):
                no += 1
                (n1x, n1y), (n2x, n2y) = normaller[i - 1], normaller[i]
                bx, by = n1x + n2x, n1y + n2y
                b = math.hypot(bx, by)
                bx, by = (bx / b, by / b) if b > 1e-6 else (n2x, n2y)
                px, py = dis_k[i]
                s.append('<circle cx="%s" cy="%s" r="0.6" fill="#111"/>' % (_f(px), _f(py)))
                s.append(_metin(px + bx * 3.2, py + by * 3.2, str(no), 2.4, renk="#b3261e"))
    else:
        atlanan = kose_toplam

    # Ada/parsel ve alan — sınıra en uzak iç noktaya
    oz = parsel["oznitelik"]
    (ex, ey), yaricap = geo.etiket_noktasi(kc[0])
    punto = max(2.4, min(4.4, yaricap * 0.45))
    s.append(_metin(ex, ey - punto * 0.6, "%s/%s" % (oz.get("ada") or "?", oz.get("parsel") or "?"),
                    punto, kalin=True))
    # Dosya kayıtlı alanı taşıyorsa etikette o yazar: kaba geometriden hesaplanan alan,
    # TKGM'nin kendi rakamının yerine konmaz. İkisi de bilgi bloğunda durur.
    alan_yazi = ("%s m²" % tr_bicim(olcu["tapu_alani_m2"]) if "tapu_alani_m2" in olcu
                 else "%s%s m²" % ("≈" if kaba else "", tr_bicim(olcu["alan_m2"])))
    s.append(_metin(ex, ey + punto * 0.7, alan_yazi, punto * 0.72))

    # Çerçeve, kuzey oku, ölçek çubuğu
    s.append('<rect x="%s" y="%s" width="%s" height="%s" fill="none" stroke="#111" '
             'stroke-width="0.35"/>' % (_f(_FX), _f(_FY), _f(_FW), _f(_FH)))
    kx, ky = _FX + _FW - 10.0, _FY + 9.0
    s.append('<path d="M%s,%s l-3,11 l3,-2.6 l3,2.6 Z" fill="#111"/>' % (_f(kx), _f(ky)))
    s.append(_metin(kx, ky - 3.0, "K", 3.4, kalin=True))
    cubuk_m = max((c for c in _CUBUKLAR if c * 1000.0 / olcek <= 45.0), default=_CUBUKLAR[0])
    cubuk_mm = cubuk_m * 1000.0 / olcek
    bx0, by0 = _FX + 6.0, _FY + _FH - 7.0
    s.append('<rect x="%s" y="%s" width="%s" height="9" fill="#fff" opacity="0.85"/>'
             % (_f(bx0 - 3), _f(by0 - 5.5), _f(cubuk_mm + 16)))
    s.append('<rect x="%s" y="%s" width="%s" height="1.4" fill="#111"/>'
             % (_f(bx0), _f(by0), _f(cubuk_mm / 2.0)))
    s.append('<rect x="%s" y="%s" width="%s" height="1.4" fill="#fff" stroke="#111" '
             'stroke-width="0.2"/>' % (_f(bx0 + cubuk_mm / 2.0), _f(by0), _f(cubuk_mm / 2.0)))
    s.append(_metin(bx0, by0 - 2.4, "0", 2.4, hale=False))
    s.append(_metin(bx0 + cubuk_mm, by0 - 2.4, "%s m" % tr_bicim(cubuk_m, 0), 2.4, hale=False))

    # Bilgi bloğu
    # Alanlar " · " ile ayrılır: SVG ardışık boşlukları teke indirir, boşlukla hizalama çalışmaz.
    satirlar = [
        "Konum: %s" % (" / ".join(oz[k] for k in ("il", "ilce", "mahalle") if oz.get(k)) or "—"),
        "Ada / Parsel: %s / %s  ·  Nitelik: %s  ·  Mevkii: %s  ·  Pafta: %s"
        % (oz.get("ada") or "—", oz.get("parsel") or "—", oz.get("nitelik") or "—",
           oz.get("mevkii") or "—", oz.get("pafta") or "—"),
    ]
    alan_satiri = "Hesap alanı: %s m²" % tr_bicim(olcu["alan_m2"])
    if "tapu_alani_m2" in olcu:
        alan_satiri += ("  ·  Dosyadaki alan: %s m²  ·  Fark: %s%s m² (%%%s)"
                        % (tr_bicim(olcu["tapu_alani_m2"]), "+" if olcu["fark_m2"] >= 0 else "",
                           tr_bicim(olcu["fark_m2"]), tr_bicim(abs(olcu["fark_yuzde"]), 2)))
    satirlar.append(alan_satiri)
    satirlar.append("Çevre: %s m  ·  Köşe: %d  ·  %s  ·  Kuzey: grid kuzeyi"
                    % (tr_bicim(olcu["cevre_m"]), kose_toplam, olcu["projeksiyon"]))
    if kaba:
        satirlar.append("Hassasiyet: koordinat ızgarası ~%s m  ·  alan ±%s m²  ·  kenar ±%s m (%%95) — "
                        "boylar yaklaşıktır" % (tr_bicim(olcu["koordinat_adimi_m"], 1),
                                               tr_bicim(olcu["alan_belirsizligi_m2"], 1),
                                               tr_bicim(olcu["kenar_belirsizligi_m"], 1)))
    if tarih:
        satirlar.append("Düzenleme: %s — ArthurLegal tkgm" % tarih)
    y = _FY + _FH + 8.0
    for satir in satirlar:
        s.append(_metin(_FX, y, satir, 3.0, "start", hale=False))
        y += 5.0

    # Köşe koordinat tablosu (isteğe bağlı)
    if koordinat_tablosu:
        noktalar = [p for c in tm for p in c[0]]
        y += 1.5
        s.append(_metin(_FX, y, "Köşe koordinatları (Y sağa, X yukarı; m)", 2.8, "start",
                        True, hale=False))
        y += 4.2
        for i, (py, px) in enumerate(noktalar[:_TABLO_SINIRI]):
            sutun, satir_no = divmod(i, 14)
            tx, ty = _FX + sutun * 58.0, y + satir_no * 3.5
            # Üç hücre üç ayrı metin: sayılar sağa yaslanır, basamaklar alt alta gelir.
            s.append(_metin(tx + 5.0, ty, str(i + 1), 2.4, "end", renk="#b3261e", hale=False))
            hane = 1 if kaba else 2
            s.append(_metin(tx + 26.0, ty, tr_bicim(py, hane), 2.4, "end", hale=False))
            s.append(_metin(tx + 50.0, ty, tr_bicim(px, hane), 2.4, "end", hale=False))
        if len(noktalar) > _TABLO_SINIRI:
            s.append(_metin(_FX + _FW, y - 4.2, "+%d köşe: disa_aktar bicim=csv"
                            % (len(noktalar) - _TABLO_SINIRI), 2.4, "end", hale=False))

    for i, satir in enumerate(UYARI):
        s.append(_metin(_FX, 286.0 + i * 3.6, satir, 2.3, "start", renk="#555", hale=False))
    s.append("</svg>")

    ozet = {"parsel": etiket(parsel), "olcek": "1/%d" % olcek, "alan_m2": olcu["alan_m2"],
            "kose_sayisi": kose_toplam, "etiketlenen_kenar": etiketli,
            "komsu_sayisi": len(komsular)}
    if atlanan:
        ozet["not"] = ("%d kenar kâğıtta 9 mm'den kısa olduğu veya köşe sayısı %d'ı aştığı için "
                       "yazılmadı; boylar `geometri` aracında." % (atlanan, _ETIKET_SINIRI))
    return "\n".join(s), ozet
