"""tkgm_aktar — parseli GeoJSON / KML / DXF / CSV olarak dışa verir.

DXF ve CSV projeksiyon (Y sağa, X yukarı) koordinatı taşır, çünkü gidecekleri
yer CAD/NetCAD'dir; GeoJSON ve KML coğrafi koordinat taşır, çünkü biçimlerin
tanımı bunu ister. Projeksiyon taşıyan iki biçim dilimi (DOM/EPSG) de yazar:
CSV'de `dom;epsg` sütunları, DXF'te KOORDINAT_SISTEMI katmanında bir metin.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict
from xml.sax.saxutils import escape

import tkgm_geo as geo
from tkgm_analiz import olc
from tkgm_parsel import etiket, temiz

BICIMLER = ("geojson", "kml", "dxf", "csv")


def _yonlu(h, saat_yonunun_tersi: bool):
    """RFC 7946 §3.1.6: dış halka saat yönünün tersine, delikler saat yönüne."""
    alan2 = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(h, h[1:] + h[:1]))
    return list(h) if (alan2 > 0) == saat_yonunun_tersi else list(reversed(h))


def _geojson(parsel: Dict[str, Any], olcu: Dict[str, Any]) -> str:
    koor = [[[[x, y] for x, y in (lambda r: r + r[:1])(_yonlu(h, i == 0))] for i, h in enumerate(c)]
            for c in parsel["cokgenler"]]
    geom = ({"type": "Polygon", "coordinates": koor[0]} if len(koor) == 1
            else {"type": "MultiPolygon", "coordinates": koor})
    ozn = dict(parsel["oznitelik"], hesap_alani_m2=olcu["alan_m2"], projeksiyon=olcu["projeksiyon"])
    return json.dumps({"type": "FeatureCollection",
                       "features": [{"type": "Feature", "geometry": geom, "properties": ozn}]},
                      ensure_ascii=False, separators=(",", ":"))


def _kml(parsel: Dict[str, Any], olcu: Dict[str, Any]) -> str:
    def halka(h):
        return " ".join("%.8f,%.8f,0" % (x, y) for x, y in h + h[:1])

    govde = []
    for c in parsel["cokgenler"]:
        ic = "".join("<innerBoundaryIs><LinearRing><coordinates>%s</coordinates></LinearRing>"
                     "</innerBoundaryIs>" % halka(h) for h in c[1:])
        govde.append("<Polygon><outerBoundaryIs><LinearRing><coordinates>%s</coordinates>"
                     "</LinearRing></outerBoundaryIs>%s</Polygon>" % (halka(c[0]), ic))
    def deger(v: Any) -> str:
        # "812.345" geri okunurken binlik ayraçlı 812345 sayılır; Türkçe yazım belirsiz değildir.
        return repr(v).replace(".", ",") if isinstance(v, float) else temiz(v)

    veri = "".join('<Data name="%s"><value>%s</value></Data>' % (escape(temiz(k)), escape(deger(v)))
                   for k, v in parsel["oznitelik"].items())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">'
            "<Document><Placemark><name>%s</name><ExtendedData>%s</ExtendedData>"
            "<MultiGeometry>%s</MultiGeometry></Placemark></Document></kml>"
            % (escape(etiket(parsel)), veri, "".join(govde)))


def _dxf(parsel: Dict[str, Any], olcu: Dict[str, Any]) -> str:
    # R12 ENTITIES-only DXF: başlık bölümü olmadan da AutoCAD/NetCAD açar ve en
    # geniş uyumluluk budur. Metin ASCII tutulur; R12'nin kod sayfası belirsizdir.
    s = ["0", "SECTION", "2", "ENTITIES"]
    for c in olcu["tm"]:
        for h in c:
            s += ["0", "POLYLINE", "8", "PARSEL", "66", "1", "70", "1"]
            for y, x in h:
                s += ["0", "VERTEX", "8", "PARSEL", "10", "%.3f" % y, "20", "%.3f" % x]
            s += ["0", "SEQEND"]
    (ey, ex), yaricap = geo.etiket_noktasi(olcu["tm"][0])
    oz = parsel["oznitelik"]
    boy = max(0.5, yaricap * 0.25)
    s += ["0", "TEXT", "8", "ETIKET", "10", "%.3f" % ey, "20", "%.3f" % ex,
          "40", "%.2f" % boy,
          "1", _ascii("%s/%s" % (oz.get("ada") or "?", oz.get("parsel") or "?"))]
    # DXF'in kendisi koordinat sistemi taşımaz; yanlış dilimde içe aktarılan parsel yüzlerce km
    # öteye düşer. Sistem ayrı katmanda görünür bir metin olarak yazılır (999 yorumu bazı
    # okuyucularda sorun çıkarır, TEXT her R12 okuyucusunda açılır).
    x0, y0, _, _ = geo.sinir_kutusu([p for c in olcu["tm"] for p in c[0]])
    s += ["0", "TEXT", "8", "KOORDINAT_SISTEMI", "10", "%.3f" % x0, "20", "%.3f" % (y0 - 2.0 * boy),
          "40", "%.2f" % (boy * 0.5), "1", _crs_ascii(olcu)]
    s += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(s) + "\n"


def _ascii(s: str) -> str:
    """DXF R12 metni: kod sayfası belirsiz, satır sonu grup kodu sayılır."""
    s = str(s).translate(str.maketrans("ıİşŞğĞüÜöÖçÇ", "iISSgGuUoOcC"))
    return re.sub(r"[^\x20-\x7e]", "", s)[:200]


def _crs_ascii(olcu: Dict[str, Any]) -> str:
    return "ITRF96 TM3 DOM%d EPSG:%d" % (olcu["dom"], geo.EPSG_TM3[olcu["dom"]])


def _tr(x: float, ondalik: int) -> str:
    return ("%.*f" % (ondalik, x)).replace(".", ",")


def _csv(parsel: Dict[str, Any], olcu: Dict[str, Any]) -> str:
    # Türkçe Excel: ";" ayraç, "," ondalık, UTF-8 BOM. Nokta ondalıklı 415123.456 orada
    # binlik ayraçlı 415.123.456 okunur. halka 0 = dış sınır, 1.. = delik.
    # dom/epsg her satırda: CSV'nin yorum satırı standardı yok, "# crs" satırı Excel ve
    # NetCAD içe aktarımında veri satırı sanılır. Sona eklenen sütun eski okuyucuyu bozmaz.
    satirlar = ["\ufeffparca;halka;no;Y_saga;X_yukari;enlem;boylam;dom;epsg"]
    dom, epsg = olcu["dom"], geo.EPSG_TM3[olcu["dom"]]
    for p, (c_tm, c_geo) in enumerate(zip(olcu["tm"], parsel["cokgenler"]), 1):
        for hno, (h_tm, h_geo) in enumerate(zip(c_tm, c_geo)):
            for no, ((y, x), (boylam, enlem)) in enumerate(zip(h_tm, h_geo), 1):
                satirlar.append("%d;%d;%d;%s;%s;%s;%s;%d;%d"
                                % (p, hno, no, _tr(y, 3), _tr(x, 3), _tr(enlem, 8),
                                   _tr(boylam, 8), dom, epsg))
    return "\n".join(satirlar) + "\n"


def aktar(parsel: Dict[str, Any], bicim: str) -> str:
    olcu = olc(parsel)
    return {"geojson": _geojson, "kml": _kml, "dxf": _dxf, "csv": _csv}[bicim](parsel, olcu)
