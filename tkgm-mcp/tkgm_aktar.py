"""tkgm_aktar — parseli GeoJSON / KML / DXF / CSV olarak dışa verir.

DXF ve CSV projeksiyon (Y sağa, X yukarı) koordinatı taşır, çünkü gidecekleri
yer CAD/NetCAD'dir; GeoJSON ve KML coğrafi koordinat taşır, çünkü biçimlerin
tanımı bunu ister.
"""

from __future__ import annotations

import json
from typing import Any, Dict
from xml.sax.saxutils import escape

import tkgm_geo as geo
from tkgm_analiz import olc
from tkgm_parsel import etiket

BICIMLER = ("geojson", "kml", "dxf", "csv")


def _geojson(parsel: Dict[str, Any], olcu: Dict[str, Any]) -> str:
    koor = [[[[x, y] for x, y in h + h[:1]] for h in c] for c in parsel["cokgenler"]]
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
    veri = "".join('<Data name="%s"><value>%s</value></Data>' % (escape(k), escape(str(v)))
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
    s += ["0", "TEXT", "8", "ETIKET", "10", "%.3f" % ey, "20", "%.3f" % ex,
          "40", "%.2f" % max(0.5, yaricap * 0.25),
          "1", "%s/%s" % (oz.get("ada") or "?", oz.get("parsel") or "?")]
    s += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(s) + "\n"


def _csv(parsel: Dict[str, Any], olcu: Dict[str, Any]) -> str:
    satirlar = ["no;Y_saga;X_yukari;enlem;boylam"]
    no = 0
    for c_tm, c_geo in zip(olcu["tm"], parsel["cokgenler"]):
        for (y, x), (boylam, enlem) in zip(c_tm[0], c_geo[0]):
            no += 1
            satirlar.append("%d;%.3f;%.3f;%.8f;%.8f" % (no, y, x, enlem, boylam))
    return "\n".join(satirlar) + "\n"


def aktar(parsel: Dict[str, Any], bicim: str) -> str:
    olcu = olc(parsel)
    return {"geojson": _geojson, "kml": _kml, "dxf": _dxf, "csv": _csv}[bicim](parsel, olcu)
