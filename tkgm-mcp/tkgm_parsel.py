"""tkgm_parsel — parsel GeoJSON / KML'sini okur ve süreç belleğinde tutar.

Bu modül ağ çağrısı yapmaz. Metin iki yoldan gelir: kullanıcının getirdiği dosya ya da
tkgm_canli'nin TKGM Parsel Sorgu'dan getirdiği GeoJSON Feature. İkisi aynı yoldan okunur;
sonraki araçlar için aralarında fark yoktur.

Parsel iç gösterimi:
    {"ref", "oznitelik": {...}, "cokgenler": [[dis_halka, ic_halka...], ...],
     "kaynak": {...}}
Halkalar (boylam, enlem) çiftleridir ve AÇIKTIR (kapanış noktası tekrarlanmaz).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import threading
import time
import json
import math
import re
import xml.etree.ElementTree as ET
import zlib
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
# Adet sınırı belleği bağlamaz: tek bir 4 MB'lık çokgen ~8 MB tutar. Bütçe köşe sayısıdır;
# tek parsel sınırı kadastro parseline fazlasıyla yeter, paylaşılan uçta CPU'yu da bağlar.
AZAMI_KOSE = 20_000
_KOSE_BUTCESI = 2_000_000
_KOSE: Dict[str, int] = {}
_BITIS: Dict[str, float] = {}   # yalnız paylaşılan kip: kayıt son kullanımdan 30 dk sonra düşer
_OMUR_SN = 1800.0
_KILIT = threading.RLock()      # ThreadingHTTPServer: sayaç ve sözlük birlikte değişir


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


_GECERSIZ = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")
_AZAMI_METIN = 500


def temiz(v: Any) -> str:
    """Öznitelik metni: XML'de geçersiz denetim karakteri ve eşleşmemiş vekil yok, satır
    sonu yok (DXF grup kodu, CSV satırı), uzunluk sınırlı (XLSX hücresi 32.767)."""
    s = _GECERSIZ.sub("", str(v))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:_AZAMI_METIN]


def _oznitelik(ham: Dict[str, Any]) -> Dict[str, Any]:
    katli = {_katla(k): v for k, v in (ham or {}).items()}
    out: Dict[str, Any] = {}
    for alan, adaylar in _ANAHTARLAR.items():
        for aday in adaylar:
            v = katli.get(aday)
            if v not in (None, "", "null"):
                out[alan] = tr_sayi(v) if alan == "tapu_alani_m2" else temiz(v)
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


_TR_BOYLAM = (25.0, 45.5)
_TR_ENLEM = (35.0, 43.0)


def _cografiye(cokgenler: List[List[List[Nokta]]], dom: Optional[int],
               k0: float) -> List[List[List[Nokta]]]:
    """Projeksiyon koordinatıyla gelen dosyayı (boylam, enlem)'e çevirir."""
    duz = [p for c in cokgenler for h in c for p in h]
    if all(abs(p[0]) <= 180.0 and abs(p[1]) <= 90.0 for p in duz):
        # Derece: Türkiye kutusunda mı? [enlem, boylam] sırası (eski EPSG:4326 eksen
        # sırası, elle yazılmış liste) sessizce %15'lik alan hatası üretiyordu.
        def icinde(b: float, e: float) -> bool:
            return _TR_BOYLAM[0] <= b <= _TR_BOYLAM[1] and _TR_ENLEM[0] <= e <= _TR_ENLEM[1]
        if not all(icinde(x, y) for x, y in duz):
            if all(icinde(y, x) for x, y in duz):
                raise ParselHatasi("Koordinatlar Türkiye dışında; [enlem, boylam] sırasıyla yazılmış "
                                   "olabilir. GeoJSON ve KML [boylam, enlem] sırası ister.")
            raise ParselHatasi("Koordinatların bir kısmı Türkiye sınırları dışında (boylam %s–%s, enlem "
                               "%s–%s); dosya yanlış parsel ya da yanlış koordinat sistemi taşıyor olabilir."
                               % (_TR_BOYLAM + _TR_ENLEM))
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

    Yerel kipte aynı dosya aynı ref'i verir (token disiplini). Paylaşılan uçta
    `sakla` bunu RASTGELE ref'le değiştirir: kamuya açık dosyadan hesaplanabilen bir
    ref, başkasının kaydını yoklamaya ve ezmeye yeterdi.
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
                    # Tam sayı "415000" float'ta "415000.0" olur; o ".0" dosyada yazılı değildir.
                    if float(v).is_integer():
                        continue
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
            # Tek innerBoundaryIs birden çok LinearRing taşıyabilir; her biri ayrı deliktir.
            icler = [lr for ib in poly.findall("innerBoundaryIs") for lr in ib.iter("LinearRing")]
            cokgenler.append([halka(dis)] + [halka(ic) for ic in icler])
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
        out = geojson_oku(govde, ad, dom, k0)
    elif govde.startswith("<"):
        out = kml_oku(govde, ad, dom, k0)
    else:
        raise ParselHatasi("Biçim tanınmadı: GeoJSON ({…}) veya KML (<…>) bekleniyor.")
    for p in out:
        if kose_sayisi(p) > AZAMI_KOSE:
            raise ParselHatasi("%s: %d köşe; parsel başına sınır %d. Kadastro parseli bu kadar köşe "
                               "taşımaz — dosya bir çizgi ya da eşyükselti katmanı olabilir."
                               % (etiket(p), kose_sayisi(p), AZAMI_KOSE))
    return out


# --------------------------------------------------------------------------- #
def kose_sayisi(parsel: Dict[str, Any]) -> int:
    return sum(len(h) for c in parsel["cokgenler"] for h in c)


def _dusur(ref: str) -> None:
    _BELLEK.pop(ref, None)
    _KOSE.pop(ref, None)
    _BITIS.pop(ref, None)


def sakla(parsel: Dict[str, Any], paylasilan: bool = False) -> None:
    """Paylaşılan uçta ref RASTGELE verilir: içerikten türeyen ref, parselin kamuya açık
    dosyasına sahip herkesin başkasının kaydını yoklamasına, okumasına ve farklı
    özniteliklerle üzerine yazmasına izin verirdi."""
    if paylasilan:
        parsel["ref"] = secrets.token_hex(8)
    kose = kose_sayisi(parsel)
    with _KILIT:
        ref = parsel["ref"]
        _dusur(ref)
        _BELLEK[ref] = parsel
        _KOSE[ref] = kose
        if paylasilan:
            _BITIS[ref] = time.monotonic() + _OMUR_SN
        while _BELLEK and (len(_BELLEK) > _BELLEK_SINIRI or sum(_KOSE.values()) > _KOSE_BUTCESI):
            _dusur(next(iter(_BELLEK)))


def getir(ref: str) -> Optional[Dict[str, Any]]:
    with _KILIT:
        p = _BELLEK.get(ref)
        if p is None:
            return None
        if ref in _BITIS:
            if time.monotonic() > _BITIS[ref]:
                _dusur(ref)
                return None
            _BITIS[ref] = time.monotonic() + _OMUR_SN
        _BELLEK.move_to_end(ref)
        return p


# --------------------------------------------------------------------------- #
# Taşınabilir ref ("ref_kodu")                                                 #
# --------------------------------------------------------------------------- #
# Bellek süreçle ölür. Paylaşılan uçta süreç yeniden başlar (deploy, bellek taşması) ya da
# istek başka bir makineye düşer; o zaman bir önceki çağrının ref'i "bellekte yok" olur.
# ref_kodu parselin kendisini taşır: sıkıştırılmış, base64url, "P1." önekli. Araçlar ref
# yerine bunu da kabul eder ve parseli yeniden kurar; ref aynı içerik özetinden yeniden
# hesaplandığı için kod çözülen parselin ref'i ilk okumadakiyle AYNIDIR.
KOD_ONEKI = "P1."
_KOD_AZAMI = 400_000          # karakter; ~5 MB'lık dosya sınırının sıkıştırılmış karşılığından geniş
_ACIK_AZAMI = 6_000_000       # açılmış JSON baytı (sıkıştırma bombasına karşı)
_KOSE_AZAMI = 200_000


def kod_mu(deger: Any) -> bool:
    return isinstance(deger, str) and deger.strip().startswith(KOD_ONEKI)


def kodla(parsel: Dict[str, Any]) -> str:
    govde = {"c": [[[[x, y] for x, y in h] for h in c] for c in parsel["cokgenler"]],
             "o": parsel["oznitelik"], "y": parsel.get("yazim") or {},
             "b": (parsel.get("kaynak") or {}).get("bicim", "")}
    ham = json.dumps(govde, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return KOD_ONEKI + base64.urlsafe_b64encode(zlib.compress(ham, 9)).decode("ascii").rstrip("=")


def koddan(kod: str) -> Dict[str, Any]:
    """ref_kodu → parsel. Güvenilmeyen girdidir: boyut, yapı ve değer aralığı denetlenir."""
    kod = (kod or "").strip()
    if not kod.startswith(KOD_ONEKI):
        raise ParselHatasi("ref_kodu '%s' ile başlamalı." % KOD_ONEKI)
    if len(kod) > _KOD_AZAMI:
        raise ParselHatasi("ref_kodu çok uzun.")
    govde = kod[len(KOD_ONEKI):]
    try:
        sikisik = base64.urlsafe_b64decode(govde + "=" * (-len(govde) % 4))
        acici = zlib.decompressobj()
        ham = acici.decompress(sikisik, _ACIK_AZAMI)
        if acici.unconsumed_tail:
            raise ParselHatasi("ref_kodu açıldığında boyut sınırını aşıyor.")
        veri = json.loads(ham.decode("utf-8"))
    except ParselHatasi:
        raise
    except (binascii.Error, zlib.error, ValueError, UnicodeDecodeError) as exc:
        raise ParselHatasi("ref_kodu çözülemedi (kesilmiş ya da değiştirilmiş olabilir): %s" % exc) from exc
    if not isinstance(veri, dict) or not isinstance(veri.get("c"), list) or not veri["c"]:
        raise ParselHatasi("ref_kodu parsel geometrisi taşımıyor.")
    kose = 0
    cokgenler: List[List[List[Nokta]]] = []
    for c in veri["c"]:
        if not isinstance(c, list) or not c:
            raise ParselHatasi("ref_kodu: bozuk çokgen.")
        halkalar = []
        for h in c:
            if not isinstance(h, list) or len(h) < 3:
                raise ParselHatasi("ref_kodu: halka üçten az nokta içeriyor.")
            halka = []
            for p in h:
                if (not isinstance(p, list) or len(p) != 2
                        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)):
                    raise ParselHatasi("ref_kodu: nokta [boylam, enlem] değil.")
                x, y = float(p[0]), float(p[1])
                if not (math.isfinite(x) and math.isfinite(y) and abs(x) <= 180.0 and abs(y) <= 90.0):
                    raise ParselHatasi("ref_kodu: koordinat aralık dışında.")
                halka.append((x, y))
            kose += len(halka)
            halkalar.append(halka)
        cokgenler.append(halkalar)
    if kose > _KOSE_AZAMI:
        raise ParselHatasi("ref_kodu çok fazla köşe taşıyor.")
    oz: Dict[str, Any] = {}
    for alan, deger in (veri.get("o") or {}).items():
        if alan not in _ANAHTARLAR or deger is None:
            continue
        if alan == "tapu_alani_m2":
            if isinstance(deger, (int, float)) and not isinstance(deger, bool) and math.isfinite(deger):
                oz[alan] = float(deger)
        elif isinstance(deger, (str, int, float)) and not isinstance(deger, bool):
            oz[alan] = str(deger)[:200]
    yazim_ham = veri.get("y") if isinstance(veri.get("y"), dict) else {}
    ondalik = yazim_ham.get("ondalik")
    yazim = {"ondalik": int(ondalik) if isinstance(ondalik, int) and 0 <= ondalik <= 12 else 12,
             "birim": "metre" if yazim_ham.get("birim") == "metre" else "derece"}
    bicim = veri.get("b") if veri.get("b") in ("geojson", "kml") else "geojson"
    return {"ref": _ref(cokgenler, oz), "oznitelik": oz, "cokgenler": cokgenler,
            "yazim": yazim, "kaynak": {"bicim": bicim, "ad": "ref_kodu"}}


def bellektekiler() -> List[Dict[str, Any]]:
    """En son kullanılan önce. Arayüz, aynı süreçte MCP ile okutulmuş parselleri buradan görür."""
    with _KILIT:
        return list(reversed(_BELLEK.values()))


def bellek_sayisi() -> int:
    with _KILIT:
        return len(_BELLEK)


def etiket(parsel: Dict[str, Any]) -> str:
    oz = parsel["oznitelik"]
    yer = "/".join(oz[k] for k in ("il", "ilce", "mahalle") if oz.get(k))
    ap = "%s/%s" % (oz.get("ada") or "?", oz.get("parsel") or "?")
    return ("%s %s" % (yer, ap)).strip()
