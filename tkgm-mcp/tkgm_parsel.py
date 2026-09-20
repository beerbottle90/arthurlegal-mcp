"""tkgm_parsel — kullanıcının getirdiği parsel dosyasını (GeoJSON / KML) okur.

Bu modül TKGM'ye bağlanmaz. Parsel Sorgu Kullanım Koşulları md. 3 uygulamanın
web servislerine izinsiz doğrudan/dolaylı erişimi yasakladığı için veri yolu
şudur: kullanıcı parseli kendi tarayıcısında sorgular, arayüzün sunduğu dışa
aktarma ile dosyayı indirir, dosyayı buraya verir.

Parsel iç gösterimi:
    {"ref", "oznitelik": {...}, "cokgenler": [[dis_halka, ic_halka...], ...],
     "kaynak": {...}}
Halkalar (boylam, enlem) çiftleridir ve AÇIKTIR (kapanış noktası tekrarlanmaz).
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from tkgm_geo import tm_geri

Nokta = Tuple[float, float]


class ParselHatasi(ValueError):
    """Dosya okunamadı ya da parsel geometrisi içermiyor."""


# Kanonik alan → dosyalarda rastlanan anahtar yazımları (katlanmış hâlde).
_ANAHTARLAR = {
    "il": ("ilad", "il", "iladi"),
    "ilce": ("ilcead", "ilce", "ilceadi"),
    "mahalle": ("mahallead", "mahalle", "mahalleadi", "mahallekoy", "koy"),
    "ada": ("adano", "ada"),
    "parsel": ("parselno", "parsel"),
    "nitelik": ("nitelik", "cins", "cinsi"),
    "mevkii": ("mevkii", "mevki"),
    "pafta": ("pafta", "paftano"),
    "tapu_alani_m2": ("alan", "tapualani", "tapualanim2", "yuzolcumu", "yuzolcum"),
    "zemin_tipi": ("zeminkmdurum", "zemintip", "zemintipi"),
    "durum": ("durum",),
    # İkinci yazımlar bu sunucunun kendi dışa aktarımıdır: aktar → oku gidiş-dönüşü
    # öznitelik kaybetmemeli.
    "gittigi_parseller": ("gittigiparselliste", "gittigiparseller"),
    "gittigi_sebep": ("gittigiparselsebep", "gittigisebep"),
}
_TR = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iisSgGuUoOcC")

_BELLEK: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_BELLEK_SINIRI = 256


def _katla(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).translate(_TR).lower())


def tr_sayi(deger: Any) -> Optional[float]:
    """"1.312,40" → 1312.4. Türkçe ve İngilizce yazımı ayırt eder."""
    if deger is None:
        return None
    if isinstance(deger, (int, float)):
        return float(deger)
    # İlk sayı belirteci alınır: bütün rakamlar toplanırsa "1.195,00 m2"nin birimi
    # sayıya yapışır ve 1195,002 okunur.
    m = re.search(r"-?\d[\d.,]*", str(deger))
    if not m:
        return None
    s = m.group(0).rstrip(".,")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _oznitelik(ham: Dict[str, Any]) -> Dict[str, Any]:
    katli = {_katla(k): v for k, v in (ham or {}).items()}
    out: Dict[str, Any] = {}
    for alan, adaylar in _ANAHTARLAR.items():
        for aday in adaylar:
            v = katli.get(aday)
            if v not in (None, "", "null"):
                out[alan] = tr_sayi(v) if alan == "tapu_alani_m2" else str(v).strip()
                break
    return out


def _halka(noktalar: List[Nokta]) -> List[Nokta]:
    temiz: List[Nokta] = []
    for p in noktalar:
        if not temiz or p != temiz[-1]:
            temiz.append(p)
    if len(temiz) > 1 and temiz[0] == temiz[-1]:
        temiz.pop()
    if len(temiz) < 3:
        raise ParselHatasi("Halka üçten az nokta içeriyor.")
    return temiz


def _cografiye(cokgenler: List[List[List[Nokta]]], dom: Optional[int],
               k0: float) -> List[List[List[Nokta]]]:
    """Projeksiyon koordinatıyla gelen dosyayı (boylam, enlem)'e çevirir."""
    duz = [p for c in cokgenler for h in c for p in h]
    if all(abs(p[0]) <= 180.0 and abs(p[1]) <= 90.0 for p in duz):
        return cokgenler
    if dom is None:
        raise ParselHatasi(
            "Koordinatlar derece değil, projeksiyon (metre) gibi görünüyor. Dilim orta "
            "meridyenini `dom` ile verin (TM 3° için 27, 30, 33, 36, 39, 42 veya 45).")
    out = []
    for c in cokgenler:
        yeni_c = []
        for h in c:
            yeni_h = []
            for a, b in h:
                # Yukarı değer (X) Türkiye'de ~4 milyon m; sağa değer (Y) 1 milyonun altında.
                saga, yukari = (a, b) if a < b else (b, a)
                enlem, boylam = tm_geri(saga, yukari, dom, k0)
                yeni_h.append((boylam, enlem))
            yeni_c.append(yeni_h)
        out.append(yeni_c)
    return out


def _ref(cokgenler: List[List[List[Nokta]]], oz: Dict[str, Any]) -> str:
    """İçerikten türeyen tanıtıcı.

    Sıra numarası değil özet: paylaşılan (uzak) sunucuda bir ref'i bilmek, o
    parselin içeriğini zaten bilmeyi gerektirir; tahminle başkasının parseline
    ulaşılamaz.
    """
    govde = json.dumps(
        [[[[round(x, 8), round(y, 8)] for x, y in h] for h in c] for c in cokgenler]
        + [oz.get("ada"), oz.get("parsel")], separators=(",", ":"))
    return hashlib.sha256(govde.encode("utf-8")).hexdigest()[:12]


def _yazim_hassasiyeti(cokgenler: List[List[List[Nokta]]]) -> Dict[str, Any]:
    """Koordinatların dosyada kaç ondalıkla YAZILDIĞI.

    Parsel Sorgu dışa aktarımı köşeleri 5 ondalığa yuvarlar (~1 m). Bunu bilmeden
    hesaplanan alan farkı kadastro hatası gibi okunur; oysa yuvarlamanın kendisidir.
    repr() en kısa gidiş-dönüş yazımını verir, yani dosyadaki basamak sayısını.
    """
    en_cok = 0
    derece = True
    for c in cokgenler:
        for h in c:
            for x, y in h:
                derece = derece and abs(x) <= 180.0 and abs(y) <= 90.0
                for v in (x, y):
                    s = repr(float(v))
                    if "e" not in s and "." in s:
                        en_cok = max(en_cok, len(s.split(".")[1]))
    return {"ondalik": min(en_cok, 12), "birim": "derece" if derece else "metre"}


def _parsel(cokgenler, ham_oznitelik, bicim, ad, dom, k0) -> Dict[str, Any]:
    yazim = _yazim_hassasiyeti(cokgenler)
    cokgenler = _cografiye(cokgenler, dom, k0)
    oz = _oznitelik(ham_oznitelik)
    return {"ref": _ref(cokgenler, oz), "oznitelik": oz, "cokgenler": cokgenler,
            "yazim": yazim, "kaynak": {"bicim": bicim, "ad": ad}}


# --------------------------------------------------------------------------- #
def geojson_oku(metin: str, ad: str = "", dom: Optional[int] = None,
                k0: float = 1.0) -> List[Dict[str, Any]]:
    try:
        veri = json.loads(metin)
    except ValueError as exc:
        raise ParselHatasi("Geçerli JSON değil: %s" % exc) from exc

    def ozellikler(v: Any):
        if not isinstance(v, dict):
            return
        tur = v.get("type")
        if tur == "FeatureCollection":
            for f in v.get("features") or []:
                yield from ozellikler(f)
        elif tur == "Feature":
            yield v.get("geometry") or {}, v.get("properties") or {}
        elif tur in ("Polygon", "MultiPolygon"):
            yield v, {}

    out = []
    for geom, ozn in ozellikler(veri):
        tur, koor = geom.get("type"), geom.get("coordinates") or []
        if tur == "Polygon":
            koor = [koor]
        elif tur != "MultiPolygon":
            continue
        cokgenler = [[_halka([(float(p[0]), float(p[1])) for p in h]) for h in c]
                     for c in koor if c]
        if cokgenler:
            out.append(_parsel(cokgenler, ozn, "geojson", ad, dom, k0))
    if not out:
        raise ParselHatasi("Dosyada Polygon/MultiPolygon geometrisi yok.")
    return out


def kml_oku(metin: str, ad: str = "", dom: Optional[int] = None,
            k0: float = 1.0) -> List[Dict[str, Any]]:
    # Parsel dışa aktarımı DTD taşımaz; taşıyan bir dosya ya bozuktur ya da varlık
    # genişletme (billion laughs) denemesidir. Ayrıştırıcıya hiç verilmez.
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", metin, re.I):
        raise ParselHatasi("DOCTYPE/ENTITY içeren KML reddedildi.")
    try:
        kok = ET.fromstring(metin.encode("utf-8"))
    except ET.ParseError as exc:
        raise ParselHatasi("Geçerli KML/XML değil: %s" % exc) from exc
    for el in kok.iter():
        el.tag = el.tag.split("}", 1)[-1]

    def halka(el) -> List[Nokta]:
        ham = (el.findtext(".//coordinates") or "").split()
        return _halka([(float(t.split(",")[0]), float(t.split(",")[1])) for t in ham])

    out = []
    for pm in kok.iter("Placemark"):
        cokgenler = []
        for poly in pm.iter("Polygon"):
            dis = poly.find("outerBoundaryIs")
            if dis is None:
                continue
            cokgenler.append([halka(dis)] + [halka(ic) for ic in poly.findall("innerBoundaryIs")])
        if not cokgenler:
            continue
        ozn: Dict[str, Any] = {}
        hucreler = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", pm.findtext("description") or "",
                              re.S | re.I)
        for k, v in zip(hucreler[::2], hucreler[1::2]):
            ozn[re.sub(r"<[^>]+>", "", k)] = re.sub(r"<[^>]+>", "", v)
        for d in pm.iter("Data"):
            ozn[d.get("name", "")] = d.findtext("value")
        for d in pm.iter("SimpleData"):
            ozn[d.get("name", "")] = d.text
        out.append(_parsel(cokgenler, ozn, "kml", ad, dom, k0))
    if not out:
        raise ParselHatasi("KML içinde Polygon taşıyan Placemark yok.")
    return out


def oku(metin: str, ad: str = "", dom: Optional[int] = None,
        k0: float = 1.0) -> List[Dict[str, Any]]:
    """Biçimi içerikten tanır: '{' ile başlayan GeoJSON, '<' ile başlayan KML."""
    govde = metin.lstrip("﻿ \t\r\n")
    if govde.startswith("{"):
        return geojson_oku(govde, ad, dom, k0)
    if govde.startswith("<"):
        return kml_oku(govde, ad, dom, k0)
    raise ParselHatasi("Biçim tanınmadı: GeoJSON ({…}) veya KML (<…>) bekleniyor.")


# --------------------------------------------------------------------------- #
def sakla(parsel: Dict[str, Any]) -> None:
    _BELLEK[parsel["ref"]] = parsel
    _BELLEK.move_to_end(parsel["ref"])
    while len(_BELLEK) > _BELLEK_SINIRI:
        _BELLEK.popitem(last=False)


def getir(ref: str) -> Optional[Dict[str, Any]]:
    p = _BELLEK.get(ref)
    if p is not None:
        _BELLEK.move_to_end(ref)
    return p


def bellektekiler() -> List[Dict[str, Any]]:
    """En son kullanılan önce. Arayüz, aynı süreçte MCP ile okutulmuş parselleri buradan görür."""
    return list(reversed(_BELLEK.values()))


def bellek_sayisi() -> int:
    return len(_BELLEK)


def etiket(parsel: Dict[str, Any]) -> str:
    oz = parsel["oznitelik"]
    yer = "/".join(oz[k] for k in ("il", "ilce", "mahalle") if oz.get(k))
    ap = "%s/%s" % (oz.get("ada") or "?", oz.get("parsel") or "?")
    return ("%s %s" % (yer, ap)).strip()
