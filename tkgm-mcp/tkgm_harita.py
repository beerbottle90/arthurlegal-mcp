"""tkgm_harita — parselin altlık harita üzerinde ve arazi üzerinde gösterimi.

İki ayrı sınır burada da geçerlidir:

1. **TKGM sınırı.** Altlık ve yükseklik için TKGM'nin hiçbir servisi (ortofoto,
   WMS, Parsel Sorgu) çağrılmaz; Kullanım Koşulları md. 3 dolaylı erişimi de
   yasaklar. Altlık yalnız OpenStreetMap'tir ve onu bu sunucu değil, HTML'i
   açan kullanıcının tarayıcısı çeker. Yükseklik yalnız Copernicus DEM'dir.
   `_http_aralik` izinli iki S3 kovası dışındaki her adresi reddeder: yasak,
   yoruma değil koda bağlıdır.

2. **Ağ sınırı.** Bu modülde ağa çıkan tek işlev `dem_izgara`dır. HTML üreten
   iki işlev saftır; test edilebilir ve paylaşılan sunucuda yan etkisizdir.

Copernicus karoları Cloud-Optimized GeoTIFF'tir. 1°×1° karo ~40 MB tutar; bir
parsel için tamamını indirmek yerine başlık okunur, kutuya değen TIFF karosu
HTTP Range ile alınır ve Deflate akışı yalnız gereken satıra kadar açılır.
TIFF elle ayrıştırılır, çünkü bu arka uç yalnız standart kütüphane kullanır.
"""

from __future__ import annotations

import html
import itertools
import json
import math
import socket
import struct
import threading
import urllib.error
import urllib.request
import zlib
from collections import OrderedDict
from http.client import HTTPException
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import tkgm_geo as geo
from tkgm_analiz import olc, tr_bicim
from tkgm_kroki import UYARI
from tkgm_parsel import etiket

Nokta = Tuple[float, float]
# (url, başlangıç, uzunluk) → bayt. Okuyucu ağı bilmez; test sahte bir dosyayı dilimler.
Getirici = Callable[[str, int, int], bytes]

KULLANICI_AJANI = "arthurlegal-tkgm/0.2 (+https://github.com/beerbottle90/arthurlegal-mcp)"
ZAMAN_ASIMI = 15.0

# GLO-30 Public'te Ermenistan/Azerbaycan ile paylaşılan 1° karolar YOKTUR (N39 E044,
# N40 E043, N40 E044, N41 E043 …: Kars, Iğdır, Çıldır). Aynı karolar GLO-90'da vardır.
# Bu yüzden "karo yok" tek başına "deniz" demek değildir; ikinci kümeye bakılır.
VERI_KUMELERI = (
    {"ad": "Copernicus DEM GLO-30", "kok": "https://copernicus-dem-30m.s3.amazonaws.com/",
     "kod": "10", "cozunurluk_m": 30, "urun": "WorldDEM-30"},
    {"ad": "Copernicus DEM GLO-90", "kok": "https://copernicus-dem-90m.s3.amazonaws.com/",
     "kod": "30", "cozunurluk_m": 90, "urun": "WorldDEM-90"},
)
# Lisansın "uyarlanmış/değiştirilmiş veri" için istediği cümle (yeniden örnekleme uyarlamadır).
_ATIF = ("produced using Copernicus %s © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH "
         "2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved")
OSM_ATIF = "© OpenStreetMap contributors"

_BLOK = 65536                 # önbellek birimi; istekler blok sınırına hizalanır
_ONBELLEK_SINIRI = 32 * 1024 * 1024
_BASLIK_BOYU = 8192           # gerçek karolarda IFD zinciri + diziler ~1,3 KB tutuyor
_EN_AZ_KAYNAK_HUCRE = 8
_AZAMI_KARO = 4               # 2×2 karodan büyük kutu parsel işi değildir

_ONBELLEK: "OrderedDict[Tuple[str, int], bytes]" = OrderedDict()
_ONBELLEK_BAYT = [0]
_KILIT = threading.Lock()     # HTTP taşıması ThreadingHTTPServer'dır
_SAYAC = {"istek": 0, "bayt": 0}


class HaritaHatasi(Exception):
    """Yükseklik verisi alınamadı ya da çözülemedi; ileti kullanıcıya gösterilebilir."""


class _KaroYok(HaritaHatasi):
    """404: karo bu veri kümesinde yok (deniz ya da yayımlanmamış)."""


class _KumeDegistir(HaritaHatasi):
    """Karo bu kümede yok ama kabası var: bütün istek kaba kümeyle yinelenir.

    30 m ile 90 m karoyu aynı mozaikte dikmek iki ayrı kafes demektir; bunun
    yerine istek tek kümeden karşılanır.
    """


# --------------------------------------------------------------------------- #
# Ağ — yalnız Copernicus kovaları, bayt aralığı, blok önbelleği                #
# --------------------------------------------------------------------------- #
def ag_istatistigi() -> Dict[str, int]:
    """Süreç boyunca yapılan istek ve indirilen bayt; önbellek isabetleri sayılmaz."""
    with _KILIT:
        return {"istek": _SAYAC["istek"], "bayt": _SAYAC["bayt"],
                "onbellek_bayt": _ONBELLEK_BAYT[0]}


def onbellegi_bosalt() -> None:
    with _KILIT:
        _ONBELLEK.clear()
        _ONBELLEK_BAYT[0] = 0


class _YonlendirmeYok(urllib.request.HTTPRedirectHandler):
    """Beyaz liste yalnız istenen adresi görür; yönlendirme izlenirse listede olmayan bir
    sunucuya bağlanılır. Kova yönlendirmez; yönlendirme hata sayılır."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise HaritaHatasi("DEM sunucusu yönlendirme döndürdü (%s); izlenmedi." % code)


_ACICI = urllib.request.build_opener(_YonlendirmeYok)


def _indir(url: str, bas: int, son: int) -> bytes:
    if not any(url.startswith(k["kok"]) for k in VERI_KUMELERI):
        raise HaritaHatasi("İzin verilmeyen adres: bu modül yalnız Copernicus DEM kovalarına bağlanır.")
    istek = urllib.request.Request(url, headers={"Range": "bytes=%d-%d" % (bas, son),
                                                 "User-Agent": KULLANICI_AJANI})
    try:
        with _ACICI.open(istek, timeout=ZAMAN_ASIMI) as yanit:
            durum = yanit.status
            govde = yanit.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _KaroYok("Karo bulunamadı (404): %s" % url.rsplit("/", 1)[-1]) from exc
        if exc.code == 416:      # dosya sonunun ötesi: kısa okuma gibi davran, çağıran karar verir
            return b""
        raise HaritaHatasi("Copernicus DEM sunucusu HTTP %d döndürdü." % exc.code) from exc
    except (urllib.error.URLError, socket.timeout, HTTPException, OSError) as exc:
        raise HaritaHatasi("Copernicus DEM sunucusuna ulaşılamadı (%s). Ağ bağlantısını kontrol "
                           "edin; yükseklik verisi olmadan harita yine üretilebilir." % exc) from exc
    if durum != 206:
        # 200 = sunucu Range'i yok saydı ve 40 MB'lık dosyanın tamamını yolluyor demektir.
        raise HaritaHatasi("Sunucu bayt aralığı isteğini desteklemedi (HTTP %d)." % durum)
    with _KILIT:
        _SAYAC["istek"] += 1
        _SAYAC["bayt"] += len(govde)
    return govde


def _http_aralik(url: str, bas: int, uzunluk: int) -> bytes:
    """Bayt aralığını blok önbelleğinden, eksik blokları tek Range isteğiyle ağdan getirir.

    Anahtar (url, blok no) olduğu için komşu parsel aynı TIFF karosunun başka bir
    satırını istediğinde ortak önek yeniden indirilmez; (bas, uzunluk) anahtarı
    bunu sağlayamazdı.
    """
    if uzunluk <= 0:
        return b""
    ilk, son = bas // _BLOK, (bas + uzunluk - 1) // _BLOK
    parcalar: Dict[int, bytes] = {}
    with _KILIT:
        for b in range(ilk, son + 1):
            veri = _ONBELLEK.get((url, b))
            if veri is not None:
                _ONBELLEK.move_to_end((url, b))
                parcalar[b] = veri
    b = ilk
    while b <= son:
        if b in parcalar:
            b += 1
            continue
        c = b
        while c + 1 <= son and (c + 1) not in parcalar:
            c += 1
        govde = _indir(url, b * _BLOK, (c + 1) * _BLOK - 1)
        with _KILIT:
            for k in range(b, c + 1):
                dilim = govde[(k - b) * _BLOK:(k - b + 1) * _BLOK]
                parcalar[k] = dilim            # kısa/boş blok dosya sonudur; o da saklanır
                if (url, k) not in _ONBELLEK:
                    _ONBELLEK_BAYT[0] += len(dilim)
                _ONBELLEK[(url, k)] = dilim
            while _ONBELLEK_BAYT[0] > _ONBELLEK_SINIRI and _ONBELLEK:
                _, eski = _ONBELLEK.popitem(last=False)
                _ONBELLEK_BAYT[0] -= len(eski)
        b = c + 1
    tam = b"".join(parcalar[k] for k in range(ilk, son + 1))
    kayma = bas - ilk * _BLOK
    return tam[kayma:kayma + uzunluk]


# --------------------------------------------------------------------------- #
# TIFF / COG okuyucu — klasik TIFF, karolu, float32, Deflate, öngörücü 1/2/3   #
# --------------------------------------------------------------------------- #
_TIP_BOYU = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8}
_TIP_HARFI = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i", 11: "f", 12: "d", 16: "Q"}
_SIKISTIRMA_DEFLATE = (8, 32946)


class _Dosya:
    """Başlık tamponu + gerektiğinde ek okuma. IFD dizileri tamponun dışına taşabilir."""

    def __init__(self, url: str, al: Getirici):
        self.url, self.al = url, al
        self.tampon = al(url, 0, _BASLIK_BOYU)

    def oku(self, bas: int, n: int) -> bytes:
        if bas + n <= len(self.tampon):
            return self.tampon[bas:bas + n]
        veri = self.al(self.url, bas, n)
        if len(veri) < n:
            raise HaritaHatasi("TIFF kesik: %d. bayttan %d bayt beklenirken %d geldi." % (bas, n, len(veri)))
        return veri


def cog_ac(url: str, al: Optional[Getirici] = None) -> Dict[str, Any]:
    """COG başlığını ve IFD zincirini okur: tam çözünürlük + küçültülmüş görünümler.

    Dönen sözlük: {"url", "bo", "seviyeler": [...], "dx", "dy", "bati_kenar", "kuzey_kenar"}.
    Kenarlar PİKSEL ALANININ kenarıdır; Copernicus karoları PixelIsPoint yazıldığı
    için bağ noktası piksel merkezidir ve yarım piksel geri alınır. Bu ayrım
    atlanırsa bütün kotlar 15 m kayar — eğimli arazide metrelerce kot hatası.
    """
    al = al or _http_aralik
    try:
        d = _Dosya(url, al)
        bas = d.oku(0, 8)
        if bas[:2] == b"II":
            bo = "<"
        elif bas[:2] == b"MM":
            bo = ">"
        else:
            raise HaritaHatasi("TIFF değil: bayt sırası imi yok.")
        sihir = struct.unpack(bo + "H", bas[2:4])[0]
        if sihir == 43:
            raise HaritaHatasi("BigTIFF desteklenmiyor (Copernicus karoları klasik TIFF'tir).")
        if sihir != 42:
            raise HaritaHatasi("TIFF değil: sihirli sayı %d." % sihir)
        konum = struct.unpack(bo + "I", bas[4:8])[0]
        seviyeler: List[Dict[str, Any]] = []
        cografi: Dict[int, Tuple] = {}
        for _ in range(32):                       # döngüsel IFD zincirine karşı sınır
            if not konum:
                break
            adet = struct.unpack(bo + "H", d.oku(konum, 2))[0]
            govde = d.oku(konum + 2, adet * 12 + 4)
            etiketler: Dict[int, Tuple] = {}
            for i in range(adet):
                no, tip, sayi = struct.unpack(bo + "HHI", govde[i * 12:i * 12 + 8])
                if tip not in _TIP_HARFI:
                    continue                      # ASCII, RATIONAL…: burada gerekmiyor
                boy = _TIP_BOYU[tip] * sayi
                if boy <= 4:
                    ham = govde[i * 12 + 8:i * 12 + 8 + boy]
                else:
                    ham = d.oku(struct.unpack(bo + "I", govde[i * 12 + 8:i * 12 + 12])[0], boy)
                etiketler[no] = struct.unpack("%s%d%s" % (bo, sayi, _TIP_HARFI[tip]), ham)
            konum = struct.unpack(bo + "I", govde[adet * 12:adet * 12 + 4])[0]
            if etiketler.get(254, (0,))[0] & 4:
                continue                          # saydamlık maskesi, yükseklik değil
            seviyeler.append(_seviye(etiketler))
            if not cografi:
                cografi = etiketler
    except (struct.error, IndexError) as exc:
        raise HaritaHatasi("TIFF başlığı çözülemedi: %s" % exc) from exc
    if not seviyeler:
        raise HaritaHatasi("TIFF içinde görüntü dizini (IFD) yok.")
    olcek, bag = cografi.get(33550), cografi.get(33922)
    if not olcek or not bag or len(bag) < 6 or olcek[0] <= 0 or olcek[1] <= 0:
        raise HaritaHatasi("GeoTIFF konum etiketleri (ModelPixelScale/ModelTiepoint) yok.")
    dx, dy = float(olcek[0]), float(olcek[1])
    anahtar = cografi.get(34735, ())
    nokta_mi = any(anahtar[i] == 1025 and anahtar[i + 3] == 2 for i in range(4, len(anahtar) - 3, 4))
    yarim = 0.5 if nokta_mi else 0.0
    return {"url": url, "bo": bo, "seviyeler": seviyeler, "dx": dx, "dy": dy,
            "bati_kenar": bag[3] - (bag[0] + yarim) * dx,
            "kuzey_kenar": bag[4] + (bag[1] + yarim) * dy}


def _seviye(e: Dict[int, Tuple]) -> Dict[str, Any]:
    def tek(no: int, varsayilan: Optional[int] = None) -> int:
        if no not in e:
            if varsayilan is None:
                raise HaritaHatasi("TIFF etiketi eksik: %d." % no)
            return varsayilan
        return int(e[no][0])

    if 322 not in e or 324 not in e:
        raise HaritaHatasi("TIFF karolu değil (şeritli); COG bekleniyordu.")
    sikistirma, ongorucu = tek(259, 1), tek(317, 1)
    if sikistirma not in _SIKISTIRMA_DEFLATE:
        raise HaritaHatasi("Desteklenmeyen sıkıştırma: %d (yalnız Deflate 8/32946)." % sikistirma)
    if ongorucu not in (1, 2, 3):
        raise HaritaHatasi("Desteklenmeyen öngörücü (Predictor): %d." % ongorucu)
    if tek(258) != 32 or tek(339, 1) != 3 or tek(277, 1) != 1:
        raise HaritaHatasi("Yalnız tek bantlı float32 yükseklik desteklenir (bit=%d, biçim=%d, bant=%d)."
                           % (tek(258), tek(339, 1), tek(277, 1)))
    s = {"en": tek(256), "boy": tek(257), "karo_en": tek(322), "karo_boy": tek(323),
         "ofset": e[324], "bayt": e[325], "ongorucu": ongorucu}
    s["karo_sutun"] = -(-s["en"] // s["karo_en"])
    if len(s["ofset"]) != len(s["bayt"]) or len(s["ofset"]) < s["karo_sutun"] * -(-s["boy"] // s["karo_boy"]):
        raise HaritaHatasi("TileOffsets/TileByteCounts görüntü boyutuyla tutarsız.")
    return s


def _satir_coz(ham: bytes, en: int, ongorucu: int, bo: str) -> Tuple[float, ...]:
    """Bir karo satırını float32 dizisine çevirir.

    Öngörücü 3 (Adobe TechNote 3): satırın baytları önce "en anlamlı baytlar başta"
    olacak biçimde dört şeride ayrılır, sonra bayt bayt fark alınır. Şerit düzeni
    dosyanın bayt sırasından bağımsızdır ve hep büyük-uçludur; küçük-uçlu dosyada
    "<f" ile açmak sessizce saçma kot üretir.
    """
    if ongorucu == 3:
        birikim = bytes(itertools.accumulate(ham, lambda a, b: (a + b) & 255))
        karisik = bytearray(4 * en)
        for k in range(4):
            karisik[k::4] = birikim[k * en:(k + 1) * en]
        return struct.unpack(">%df" % en, bytes(karisik))
    if ongorucu == 2:
        # Yatay fark 32 bitlik sözcükler üzerindedir (libtiff float32'yi de böyle işler).
        toplam = itertools.accumulate(struct.unpack("%s%dI" % (bo, en), ham),
                                      lambda a, b: (a + b) & 0xFFFFFFFF)
        return struct.unpack("%s%df" % (bo, en), struct.pack("%s%dI" % (bo, en), *toplam))
    return struct.unpack("%s%df" % (bo, en), ham)


def cog_pencere(cog: Dict[str, Any], seviye_no: int, sutun0: int, satir0: int,
                sutun1: int, satir1: int, al: Optional[Getirici] = None) -> List[List[float]]:
    """[satir0, satir1) × [sutun0, sutun1) penceresini okur; yalnız değen TIFF karoları iner.

    Deflate akışı ortasından açılamaz ama başından istenen satıra kadar açılabilir.
    Gerçek karolar 1024×1024 ve ~2,4 MB olduğu için önce tahmini önek, yetmezse
    kalanı istenir: karonun üst yarısındaki bir parsel için indirme yarıya iner.
    Öngörücü satır bazında çalıştığından yalnız penceredeki satırlar çözülür;
    4 milyon baytlık karoyu saf Python'da baştan sona çözmek saniyeler sürerdi.
    """
    al = al or _http_aralik
    s = cog["seviyeler"][seviye_no]
    if not (0 <= sutun0 < sutun1 <= s["en"] and 0 <= satir0 < satir1 <= s["boy"]):
        raise HaritaHatasi("Pencere görüntünün dışında.")
    ke, kb = s["karo_en"], s["karo_boy"]
    satir_bayt = ke * 4
    cikti: List[List[float]] = [[] for _ in range(satir1 - satir0)]
    for ky in range(satir0 // kb, (satir1 - 1) // kb + 1):
        r0, r1 = max(satir0, ky * kb) - ky * kb, min(satir1, (ky + 1) * kb) - ky * kb
        for kx in range(sutun0 // ke, (sutun1 - 1) // ke + 1):
            c0, c1 = max(sutun0, kx * ke) - kx * ke, min(sutun1, (kx + 1) * ke) - kx * ke
            no = ky * s["karo_sutun"] + kx
            # Görüntü karodan kısaysa (GLO-90: 1200 satır, 2048'lik karo) dolgu satırları
            # sıkışınca yer tutmaz; oran gerçek satır sayısına göre kurulur.
            dolu = min(kb, s["boy"] - ky * kb)
            ham = _karo_oneki(al, cog["url"], s["ofset"][no], s["bayt"][no], r1 * satir_bayt,
                              min(1.0, float(r1) / dolu))
            for r in range(r0, r1):
                satir = _satir_coz(ham[r * satir_bayt:(r + 1) * satir_bayt], ke, s["ongorucu"], cog["bo"])
                cikti[ky * kb + r - satir0].extend(satir[c0:c1])
    return cikti


def _karo_oneki(al: Getirici, url: str, ofset: int, boy: int, gereken: int, oran: float) -> bytes:
    if boy <= 0:
        raise HaritaHatasi("TIFF karosu boş (seyrek dosya desteklenmiyor).")
    cozucu = zlib.decompressobj()
    cozulen = bytearray()
    konum = 0
    # Sıkıştırma oranı satırdan satıra değişir (düz alan iyi sıkışır); %20 pay + bir blok.
    adim = min(boy, int(boy * oran * 1.2) + _BLOK)
    try:
        while len(cozulen) < gereken and konum < boy:
            parca = al(url, ofset + konum, adim)
            if not parca:
                break
            cozulen += cozucu.decompress(parca, gereken - len(cozulen))
            konum += len(parca)
            adim = boy - konum
    except zlib.error as exc:
        raise HaritaHatasi("TIFF karosu açılamadı (Deflate): %s" % exc) from exc
    if len(cozulen) < gereken:
        raise HaritaHatasi("TIFF karosu beklenenden kısa: %d / %d bayt." % (len(cozulen), gereken))
    return bytes(cozulen)


# --------------------------------------------------------------------------- #
# Yeniden örnekleme ve yükseklik ızgarası                                      #
# --------------------------------------------------------------------------- #
def cift_dogrusal(z: Sequence[Sequence[float]], satir: float, sutun: float) -> float:
    """Kesirli (satır, sütun) konumunda çift doğrusal ara değer; kenarda kenetlenir."""
    ny, nx = len(z), len(z[0])
    satir = min(max(satir, 0.0), ny - 1.0)
    sutun = min(max(sutun, 0.0), nx - 1.0)
    i, j = min(int(satir), max(ny - 2, 0)), min(int(sutun), max(nx - 2, 0))
    v, u = satir - i, sutun - j
    i1, j1 = min(i + 1, ny - 1), min(j + 1, nx - 1)
    ust = z[i][j] * (1.0 - u) + z[i][j1] * u
    alt = z[i1][j] * (1.0 - u) + z[i1][j1] * u
    return ust * (1.0 - v) + alt * v


def kot_al(izgara: Dict[str, Any], enlem: float, boylam: float) -> float:
    """`dem_izgara` çıktısından bir noktanın kotu (çift doğrusal)."""
    e, b = izgara["enlemler"], izgara["boylamlar"]
    satir = (e[0] - enlem) / (e[0] - e[-1]) * (len(e) - 1) if len(e) > 1 else 0.0
    sutun = (boylam - b[0]) / (b[-1] - b[0]) * (len(b) - 1) if len(b) > 1 else 0.0
    return cift_dogrusal(izgara["z"], satir, sutun)


def metre_derece(enlem: float, boylam: float) -> Tuple[float, float]:
    """(1° boylam, 1° enlem) kaç metre — noktayı orta meridyen alan yerel TM'den.

    Küre yaklaşımı (111 320·cos φ) 40° enleminde %0,2 şaşar; paketteki Krüger
    serisi hazırken ikinci bir Dünya modeli taşımak iki ayrı sayı demektir.
    """
    h = 5e-4
    y0, x0 = geo.tm_ileri(enlem, boylam - h, boylam)
    y1, x1 = geo.tm_ileri(enlem, boylam + h, boylam)
    _, xa = geo.tm_ileri(enlem - h, boylam, boylam)
    _, xu = geo.tm_ileri(enlem + h, boylam, boylam)
    return math.hypot(y1 - y0, x1 - x0) / (2 * h), (xu - xa) / (2 * h)


def parsel_kutusu(parsel: Dict[str, Any], komsular: Optional[List[Dict[str, Any]]] = None,
                  pay_m: float = 60.0) -> Tuple[float, float, float, float]:
    """(batı, güney, doğu, kuzey): parsel + komşular + metre cinsinden pay."""
    duz = [p for q in [parsel] + list(komsular or []) for c in q["cokgenler"] for p in c[0]]
    b0, e0, b1, e1 = geo.sinir_kutusu(duz)
    mb, me = metre_derece((e0 + e1) / 2.0, (b0 + b1) / 2.0)
    return b0 - pay_m / mb, e0 - pay_m / me, b1 + pay_m / mb, e1 + pay_m / me


def karo_adi(enlem_tam: int, boylam_tam: int, kod: str = "10") -> str:
    """Karo adı güneybatı köşesidir: N39_00_E032_00 → 39–40°K, 32–33°D."""
    return "Copernicus_DSM_COG_%s_%s%02d_00_%s%03d_00_DEM" % (
        kod, "N" if enlem_tam >= 0 else "S", abs(enlem_tam),
        "E" if boylam_tam >= 0 else "W", abs(boylam_tam))


def _karo_url(kume: Dict[str, Any], enlem_tam: int, boylam_tam: int) -> str:
    ad = karo_adi(enlem_tam, boylam_tam, kume["kod"])
    return "%s%s/%s.tif" % (kume["kok"], ad, ad)


def dem_izgara(bati: float, guney: float, dogu: float, kuzey: float, hedef_hucre: int = 64,
               al: Optional[Getirici] = None) -> Dict[str, Any]:
    """Kutu için düzenli yükseklik ızgarası. `z[i][j]` = (enlemler[i], boylamlar[j]) kotu;
    enlemler KUZEYDEN GÜNEYE azalır (raster düzeni), boylamlar batıdan doğuya artar.

    `al` yalnız test içindir: ağ yerine bayt dilimleyen bir işlev verilir.
    """
    try:
        bati, guney, dogu, kuzey = float(bati), float(guney), float(dogu), float(kuzey)
        hedef = max(8, min(256, int(hedef_hucre)))
    except (TypeError, ValueError) as exc:
        raise HaritaHatasi("Kutu sayısal olmalı: bati, guney, dogu, kuzey.") from exc
    if not (-180.0 <= bati < dogu <= 180.0 and -85.0 <= guney < kuzey <= 85.0):
        raise HaritaHatasi("Geçersiz kutu: bati < dogu, guney < kuzey ve derece cinsinden olmalı "
                           "(sıra: boylam, enlem, boylam, enlem).")
    al = al or _http_aralik
    onceki = ag_istatistigi()
    son_hata: Optional[HaritaHatasi] = None
    for sira, kume in enumerate(VERI_KUMELERI):
        try:
            out = _izgara_kumeden(kume, bati, guney, dogu, kuzey, hedef, al,
                                  VERI_KUMELERI[sira + 1:])
        except _KumeDegistir as exc:
            son_hata = exc
            continue
        simdi = ag_istatistigi()
        out["indirilen_bayt"] = simdi["bayt"] - onceki["bayt"]
        out["istek_sayisi"] = simdi["istek"] - onceki["istek"]
        return out
    raise HaritaHatasi(str(son_hata) if son_hata else "Yükseklik verisi bulunamadı.")


def _izgara_kumeden(kume: Dict[str, Any], bati: float, guney: float, dogu: float, kuzey: float,
                    hedef: int, al: Getirici, yedekler: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    # Küçük parsel 30 m'lik veride tek hücreye sığar; tek hücreden eğim çıkmaz.
    # Kutu, kaynakta en az 8×8 hücre kalacak biçimde ortadan genişletilir.
    adim = kume["cozunurluk_m"] / 30.0 / 3600.0
    eksik_b = _EN_AZ_KAYNAK_HUCRE * adim - (dogu - bati)
    eksik_e = _EN_AZ_KAYNAK_HUCRE * adim - (kuzey - guney)
    if eksik_b > 0:
        bati, dogu = bati - eksik_b / 2.0, dogu + eksik_b / 2.0
    if eksik_e > 0:
        guney, kuzey = guney - eksik_e / 2.0, kuzey + eksik_e / 2.0
    # Karo seçimi piksel MERKEZİNE göredir: T karosunun merkezleri boylamda [T, T+1),
    # enlemde (T, T+1] aralığındadır (en kuzey satır tam T+1'dedir). Son merkez ile
    # dikiş arasındaki nokta, ara değer için komşu karonun ilk sütununu/satırını ister;
    # o karo açılmazsa dikiş boyunca 30 m'lik düz bir şerit oluşur.
    e_tamlar = list(range(int(math.ceil(guney - adim + 1e-12)) - 1, int(math.ceil(kuzey))))
    b_tamlar = list(range(int(math.floor(bati)), int(math.floor(dogu + adim - 1e-12)) + 1))
    if len(e_tamlar) * len(b_tamlar) > _AZAMI_KARO:
        raise HaritaHatasi("Kutu çok büyük: %d adet 1° karoya değiyor (en çok %d). Parsel ölçeğinde "
                           "bir kutu verin." % (len(e_tamlar) * len(b_tamlar), _AZAMI_KARO))

    coglar: Dict[Tuple[int, int], Optional[Dict[str, Any]]] = {}
    uyarilar: List[str] = []
    for et in e_tamlar:
        for bt in b_tamlar:
            try:
                coglar[(et, bt)] = cog_ac(_karo_url(kume, et, bt), al)
            except _KaroYok:
                for yedek in yedekler:
                    try:
                        cog_ac(_karo_url(yedek, et, bt), al)
                    except _KaroYok:
                        continue
                    raise _KumeDegistir("%s karosu %s içinde yok; %s kullanılacak."
                                        % (karo_adi(et, bt, kume["kod"]), kume["ad"], yedek["ad"]))
                # Hiçbir kümede yok: Copernicus belgesine göre açık deniz, kot 0 alınır.
                coglar[(et, bt)] = None
                uyarilar.append("%s karosu Copernicus DEM'de yok (açık deniz); o bölüm 0 m alındı."
                                % karo_adi(et, bt, kume["kod"]))
    varlar = [c for c in coglar.values() if c is not None]
    if not varlar:
        raise HaritaHatasi("Bu kutu için Copernicus DEM karosu yok (açık deniz ya da kapsam dışı).")
    ilk = varlar[0]
    if any(abs(c["dx"] - ilk["dx"]) > 1e-12 or abs(c["dy"] - ilk["dy"]) > 1e-12 or
           len(c["seviyeler"]) != len(ilk["seviyeler"]) for c in varlar):
        raise HaritaHatasi("Kutu, piksel aralığı farklı iki karoya değiyor (50° enlem kuşağı sınırı); "
                           "kutuyu tek kuşakta tutun.")

    # Seviye seçimi: uzun kenarda en az `hedef` kaynak hücresi bırakan EN KABA görünüm.
    uzun_px = max((dogu - bati) / ilk["dx"], (kuzey - guney) / ilk["dy"])
    seviye_no, kat = 0, 1.0
    for no, s in enumerate(ilk["seviyeler"]):
        k = float(ilk["seviyeler"][0]["en"]) / s["en"]
        if no and uzun_px / k >= hedef and k > kat:
            seviye_no, kat = no, k
    sv = ilk["seviyeler"][seviye_no]
    dx = ilk["dx"] * ilk["seviyeler"][0]["en"] / sv["en"]
    dy = ilk["dy"] * ilk["seviyeler"][0]["boy"] / sv["boy"]

    # Mozaik: ortak kafeste (genel sütun, genel satır). Kafes başlangıcı en batı/kuzey karo.
    bk0 = min(c["bati_kenar"] for c in varlar)
    kk0 = max(c["kuzey_kenar"] for c in varlar)
    # İstenen pencere (kesirli genel piksel; merkez = kenar + (i + 0,5)·d), ±1 piksel pay.
    gs0 = int(math.floor((bati - bk0) / dx - 0.5)) - 1
    gs1 = int(math.ceil((dogu - bk0) / dx - 0.5)) + 2
    gr0 = int(math.floor((kk0 - kuzey) / dy - 0.5)) - 1
    gr1 = int(math.ceil((kk0 - guney) / dy - 0.5)) + 2
    mozaik: Dict[Tuple[int, int], float] = {}
    deniz: List[Tuple[int, int, int, int]] = []        # karosu olmayan alanlar: r0, s0, r1, s1
    for (et, bt), c in coglar.items():
        if c is None:
            # Olmayan karonun kafesteki yeri, var olan bir karonun tam dereceye göre kaymasından bulunur.
            ds = int(round((bt + ilk["bati_kenar"] - round(ilk["bati_kenar"]) - bk0) / dx))
            dr = int(round((kk0 - (et + 1 + ilk["kuzey_kenar"] - round(ilk["kuzey_kenar"]))) / dy))
            deniz.append((dr, ds, dr + sv["boy"], ds + sv["en"]))
            continue
        ks = (c["bati_kenar"] - bk0) / dx
        kr = (kk0 - c["kuzey_kenar"]) / dy
        if abs(ks - round(ks)) > 1e-6 or abs(kr - round(kr)) > 1e-6:
            raise HaritaHatasi("Komşu karoların piksel kafesleri hizalı değil; kutuyu tek karoda tutun.")
        ks, kr = int(round(ks)), int(round(kr))
        s0, s1 = max(gs0 - ks, 0), min(gs1 - ks, sv["en"])
        r0, r1 = max(gr0 - kr, 0), min(gr1 - kr, sv["boy"])
        if s0 >= s1 or r0 >= r1:
            continue
        pencere = cog_pencere(c, seviye_no, s0, r0, s1, r1, al)
        for i, satir in enumerate(pencere):
            for j, v in enumerate(satir):
                if v != v or v in (float("inf"), float("-inf")) or v < -1000.0 or v > 9000.0:
                    raise HaritaHatasi("Yükseklik verisinde geçersiz değer (%r); karo bozuk olabilir." % v)
                mozaik[(r0 + kr + i, s0 + ks + j)] = v
    if not mozaik:
        raise HaritaHatasi("Kutu hiçbir karonun veri alanına düşmedi.")
    r_en_az, r_en_cok = min(k[0] for k in mozaik), max(k[0] for k in mozaik)
    s_en_az, s_en_cok = min(k[1] for k in mozaik), max(k[1] for k in mozaik)

    def kaynak(r: int, s: int) -> float:
        v = mozaik.get((r, s))
        if v is not None:
            return v
        if any(a <= r < c and b <= s < d for a, b, c, d in deniz):
            return 0.0
        # Açılmamış komşu karonun ya da veri kümesinin kenarı: "0 m" değil, en yakın geçerli hücre.
        return mozaik.get((min(max(r, r_en_az), r_en_cok), min(max(s, s_en_az), s_en_cok)), 0.0)

    orta = (guney + kuzey) / 2.0
    mb, me = metre_derece(orta, (bati + dogu) / 2.0)
    gen_m, yuk_m = (dogu - bati) * mb, (kuzey - guney) * me
    hucre = max(gen_m, yuk_m) / (hedef - 1)
    nx = max(2, int(round(gen_m / hucre)) + 1)
    ny = max(2, int(round(yuk_m / hucre)) + 1)
    boylamlar = [bati + (dogu - bati) * j / (nx - 1) for j in range(nx)]
    enlemler = [kuzey - (kuzey - guney) * i / (ny - 1) for i in range(ny)]
    z: List[List[float]] = []
    for enlem in enlemler:
        fr = (kk0 - enlem) / dy - 0.5
        r = int(math.floor(fr))
        v = fr - r
        satir = []
        for boylam in boylamlar:
            fs = (boylam - bk0) / dx - 0.5
            s = int(math.floor(fs))
            u = fs - s
            ust = kaynak(r, s) * (1.0 - u) + kaynak(r, s + 1) * u
            alt = kaynak(r + 1, s) * (1.0 - u) + kaynak(r + 1, s + 1) * u
            satir.append(round(ust * (1.0 - v) + alt * v, 2))
        z.append(satir)

    out: Dict[str, Any] = {
        "enlemler": [round(e, 7) for e in enlemler],
        "boylamlar": [round(b, 7) for b in boylamlar],
        "z": z,
        "kaynak_cozunurluk_m": kume["cozunurluk_m"],
        "kaynak": kume["ad"],
        "atif": _ATIF % kume["urun"],
        "okunan_cozunurluk_m": round(kume["cozunurluk_m"] * kat, 1),
        "hucre_m": round(hucre, 2),
        "kutu": [round(bati, 7), round(guney, 7), round(dogu, 7), round(kuzey, 7)],
    }
    if uyarilar:
        out["uyari"] = uyarilar
    if kume is not VERI_KUMELERI[0]:
        out.setdefault("uyari", []).append(
            "Bu bölgenin 30 m'lik karosu kamuya açık GLO-30'da yok (Ermenistan/Azerbaycan sınır "
            "karoları); 90 m'lik GLO-90 kullanıldı.")
    return out


# --------------------------------------------------------------------------- #
# Arazi özeti                                                                  #
# --------------------------------------------------------------------------- #
_YONLER = ("K", "KD", "D", "GD", "G", "GB", "B", "KB")
_DUZ_ESIGI = 1.0     # %1'in altındaki ortalama eğimde bakı, verinin gürültüsünden ayırt edilemez


def _etiket_noktasi(parsel: Dict[str, Any]) -> Nokta:
    """Sınıra en uzak iç nokta, (boylam, enlem). Metrede aranır: derece ızgarası
    40° enleminde doğu-batıyı %23 kısa görür ve "en uzak" nokta kayar."""
    olcu = olc(parsel)
    (y, x), _ = geo.etiket_noktasi(olcu["tm"][0])
    enlem, boylam = geo.tm_geri(y, x, olcu["dom"])
    return boylam, enlem


def arazi_ozeti(parsel: Dict[str, Any], izgara: Dict[str, Any]) -> Dict[str, Any]:
    """Parsel içindeki kot, eğim ve bakı. Eğim/bakı metre cinsinden sonlu farktan gelir."""
    enlemler, boylamlar, z = izgara["enlemler"], izgara["boylamlar"], izgara["z"]
    ny, nx = len(enlemler), len(boylamlar)
    if ny < 2 or nx < 2 or len(z) != ny or any(len(s) != nx for s in z):
        raise HaritaHatasi("Izgara en az 2×2 olmalı ve z boyutları eksenlerle uyuşmalı.")
    mb, me = metre_derece((enlemler[0] + enlemler[-1]) / 2.0, (boylamlar[0] + boylamlar[-1]) / 2.0)
    hx = (boylamlar[-1] - boylamlar[0]) / (nx - 1) * mb       # doğuya hücre, m
    hy = (enlemler[0] - enlemler[-1]) / (ny - 1) * me         # kuzeye hücre, m (satırlar güneye iner)

    def egim(i: int, j: int) -> Nokta:
        """(∂z/∂doğu, ∂z/∂kuzey); içeride merkezî, kenarda tek yanlı fark."""
        j0, j1 = max(j - 1, 0), min(j + 1, nx - 1)
        i0, i1 = max(i - 1, 0), min(i + 1, ny - 1)
        return ((z[i][j1] - z[i][j0]) / ((j1 - j0) * hx),
                (z[i0][j] - z[i1][j]) / ((i1 - i0) * hy))

    ornekler: List[Tuple[float, float, float]] = []            # (kot, gx, gy)
    pb0, pe0, pb1, pe1 = geo.sinir_kutusu([p for c in parsel["cokgenler"] for p in c[0]])
    for i, enlem in enumerate(enlemler):
        if not pe0 <= enlem <= pe1:
            continue                  # 256×256 ızgarada 65 bin nokta-çokgen sınaması yerine birkaç yüz
        for j, boylam in enumerate(boylamlar):
            if pb0 <= boylam <= pb1 and any(geo.cokgen_icinde((boylam, enlem), c)
                                            for c in parsel["cokgenler"]):
                ornekler.append((z[i][j],) + egim(i, j))
    yontem = "izgara"
    if len(ornekler) < 4:
        # Parsel hücreden küçük: içine ızgara noktası düşmez. Köşeler + etiket noktası
        # ara değerle örneklenir; eğim en yakın düğümün sonlu farkıdır.
        yontem = "kose+etiket"
        noktalar = [p for c in parsel["cokgenler"] for p in c[0]] + [_etiket_noktasi(parsel)]
        for boylam, enlem in noktalar:
            fi = (enlemler[0] - enlem) / (enlemler[0] - enlemler[-1]) * (ny - 1)
            fj = (boylam - boylamlar[0]) / (boylamlar[-1] - boylamlar[0]) * (nx - 1)
            if not (-0.5 <= fi <= ny - 0.5 and -0.5 <= fj <= nx - 0.5):
                continue
            i, j = min(max(int(round(fi)), 0), ny - 1), min(max(int(round(fj)), 0), nx - 1)
            ornekler.append((cift_dogrusal(z, fi, fj),) + egim(i, j))
    if not ornekler:
        raise HaritaHatasi("Parsel yükseklik ızgarasının dışında; kutuyu parselden üretin "
                           "(parsel_kutusu).")

    kotlar = [o[0] for o in ornekler]
    n = float(len(ornekler))
    ort_egim = sum(math.hypot(o[1], o[2]) for o in ornekler) / n * 100.0
    gx, gy = sum(o[1] for o in ornekler) / n, sum(o[2] for o in ornekler) / n
    if ort_egim < _DUZ_ESIGI or math.hypot(gx, gy) < 1e-9:
        baki, baki_derece = "düz", None
    else:
        # Bakı yamacın BAKTIĞI yöndür: iniş yönü, kuzeyden saat yönünde.
        baki_derece = math.degrees(math.atan2(-gx, -gy)) % 360.0
        baki = _YONLER[int((baki_derece + 22.5) // 45.0) % 8]
    kaynak_m = izgara.get("okunan_cozunurluk_m") or izgara.get("kaynak_cozunurluk_m") or 30
    out: Dict[str, Any] = {
        "min_kot_m": round(min(kotlar), 1), "max_kot_m": round(max(kotlar), 1),
        "ortalama_kot_m": round(sum(kotlar) / n, 1),
        "kot_farki_m": round(max(kotlar) - min(kotlar), 1),
        "ortalama_egim_yuzde": round(ort_egim, 1),
        "hakim_baki": baki,
        "ornek_sayisi": len(ornekler), "yontem": yontem,
        "kaynak": izgara.get("kaynak", "Copernicus DEM GLO-30"),
        "not": ("Kotlar %s m çözünürlüklü Copernicus DEM'den ara değerle alınmıştır. Bu bir YÜZEY "
                "modelidir (DSM): bina ve ağaç yüksekliğini içerir, çıplak zemin değildir. Kentsel "
                "ve küçük parsellerde eğim/bakı kabadır; halihazır harita, plankote veya arazi "
                "ölçümü yerine geçmez." % tr_bicim(float(kaynak_m), 0)),
    }
    if baki_derece is not None:
        out["baki_derece"] = round(baki_derece, 0)
    return out


# --------------------------------------------------------------------------- #
# HTML — ortak parçalar                                                        #
# --------------------------------------------------------------------------- #
def _js(veri: Any) -> str:
    """<script> içine gömülecek JSON.

    HTML ayrıştırıcısı betiği JSON'dan ÖNCE okur: öznitelikteki bir `</script>`
    dizgiyi değil betiği kapatır. `<`, `>`, `&` kaçışlanınca ayrıştırıcının
    görebileceği bir etiket kalmaz; U+2028/2029 eski motorlarda satır sonudur.
    """
    return (json.dumps(veri, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _h(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _ada_parsel(parsel: Dict[str, Any]) -> str:
    oz = parsel["oznitelik"]
    return "%s/%s" % (oz.get("ada") or "?", oz.get("parsel") or "?")


def _kart_satirlari(parsel: Dict[str, Any], olcu: Dict[str, Any]) -> List[Tuple[str, str]]:
    oz = parsel["oznitelik"]
    alan = "%s m² (hesap)" % tr_bicim(olcu["alan_m2"])
    if "tapu_alani_m2" in olcu:
        alan += " · dosyada %s m²" % tr_bicim(olcu["tapu_alani_m2"])
    return [("Konum", " / ".join(str(oz[k]) for k in ("il", "ilce", "mahalle") if oz.get(k)) or "—"),
            ("Ada / Parsel", _ada_parsel(parsel)),
            ("Nitelik", str(oz.get("nitelik") or "—")),
            ("Alan", alan)]


def _tablo(satirlar: Sequence[Tuple[str, str]]) -> str:
    return "<table>%s</table>" % "".join("<tr><th>%s</th><td>%s</td></tr>" % (_h(k), _h(v))
                                          for k, v in satirlar)


_ORTAK_CSS = """html,body{margin:0;height:100%;font-family:Arial,Helvetica,sans-serif}
.kart{position:absolute;z-index:1000;top:10px;left:10px;max-width:330px;background:rgba(255,255,255,.94);
border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.35);padding:10px 12px;font-size:12.5px;color:#111;line-height:1.35}
.kart h1{font-size:14px;margin:0 0 6px}.kart table{border-collapse:collapse}
.kart th{text-align:left;font-weight:normal;color:#555;padding:1px 10px 1px 0;vertical-align:top;white-space:nowrap}
.kart td{padding:1px 0}.kart p{margin:7px 0 0;font-size:10.5px;color:#555}
@media (max-width:600px){.kart{max-width:none;right:10px;font-size:11.5px}}"""


# --------------------------------------------------------------------------- #
# Altlık harita (Leaflet + OpenStreetMap)                                      #
# --------------------------------------------------------------------------- #
_LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/"
# SRI özetleri indirilen dosyalardan sha512 ile hesaplandı ve cdnjs API'sinin verdiğiyle
# karşılaştırıldı. Sürüm değişirse ikisi birlikte değişmeli: yanlış özet sayfayı boş bırakır.
_LEAFLET_CSS_SRI = ("sha512-h9FcoyWjHcOcmEVkxOfTLnmZFWIH0iZhZT1H2TbOq55xssQGEJHEaIm+"
                    "PgoUaZbRvQTNTluNOEfb1ZRy6D3BOw==")
_LEAFLET_JS_SRI = ("sha512-puJW3E/qXDqYp9IfhAI54BJEaWIfloJ7JWs7OeD5i6ruC9JZL1gERT1wjtwXFlh7"
                   "CjE7ZJ+/vcRZRkIYIb6p4g==")
_OSM_KARO = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def _leaflet_halkalari(parsel: Dict[str, Any]) -> List[List[List[List[float]]]]:
    """Leaflet [enlem, boylam] ister; GeoJSON sırasıyla verilirse parsel Somali açıklarına düşer."""
    return [[[[round(enlem, 7), round(boylam, 7)] for boylam, enlem in h] for h in c]
            for c in parsel["cokgenler"]]


_HARITA_JS = """(function(){
var V=__VERI__;
var h=L.map('harita',{maxZoom:19,zoomSnap:0.25});
L.tileLayer(V.karo,{maxZoom:19,attribution:V.atif}).addTo(h);
L.control.scale({metric:true,imperial:false}).addTo(h);
// Metin her yerde textContent ile yazılır: Leaflet dizgi içeriği HTML olarak yorumlar.
function yazi(s,alt){var d=document.createElement('div');var b=document.createElement('b');
b.textContent=s;d.appendChild(b);if(alt){d.appendChild(document.createElement('br'));
d.appendChild(document.createTextNode(alt));}return d;}
V.komsular.forEach(function(k){
L.polygon(k.h,{color:'#6b6b6b',weight:1,fillColor:'#9a9a9a',fillOpacity:0.08})
.bindTooltip(yazi(k.ad),{sticky:true}).addTo(h);});
var p=L.polygon(V.parsel,{color:'#d81b60',weight:4,fillColor:'#ffd54f',fillOpacity:0.25}).addTo(h);
L.tooltip({permanent:true,direction:'center',className:'etiket',interactive:false})
.setLatLng(V.etiket).setContent(yazi(V.ad,V.alan)).addTo(h);
h.fitBounds(p.getBounds(),{padding:[70,70]});
})();"""


def harita_html(parsel: Dict[str, Any], komsular: Optional[List[Dict[str, Any]]] = None) -> str:
    """Tek dosyalık Leaflet haritası: OSM altlığı, vurgulu parsel, gri komşular.

    Altlık karoları ve Leaflet betiği dosyayı AÇAN tarayıcı tarafından çekilir; bu işlev
    ağa çıkmaz. Çevrimdışıyken Leaflet yüklenemez ve harita çizilmez — çevrimdışı
    çizim için kroki (SVG) kullanılır.
    """
    olcu = olc(parsel)
    boylam, enlem = _etiket_noktasi(parsel)
    veri = {
        "karo": _OSM_KARO, "atif": OSM_ATIF,
        "parsel": _leaflet_halkalari(parsel),
        "komsular": [{"ad": _ada_parsel(k), "h": _leaflet_halkalari(k)}
                     for k in (komsular or []) if k.get("ref") != parsel.get("ref")],
        "etiket": [round(enlem, 7), round(boylam, 7)],
        "ad": _ada_parsel(parsel), "alan": "%s m²" % tr_bicim(olcu["alan_m2"]),
    }
    kart = ('<div class="kart"><h1>%s</h1>%s<p>%s</p><p>Altlık: %s</p></div>'
            % (_h(etiket(parsel)), _tablo(_kart_satirlari(parsel, olcu)),
               "<br>".join(_h(u) for u in UYARI), _h(OSM_ATIF)))
    return "\n".join([
        "<!DOCTYPE html>", '<html lang="tr"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Parsel haritası — %s</title>" % _h(etiket(parsel)),
        '<link rel="stylesheet" href="%sleaflet.min.css" integrity="%s" crossorigin="anonymous">'
        % (_LEAFLET, _LEAFLET_CSS_SRI),
        "<style>%s\n#harita{position:absolute;inset:0;background:#e9e9e6}"
        ".etiket{background:rgba(255,255,255,.9);border:1px solid #d81b60;color:#111;font-size:12px;"
        "text-align:center;padding:2px 6px;box-shadow:none}.etiket:before{display:none}</style>" % _ORTAK_CSS,
        '</head><body><div id="harita"></div>', kart,
        '<script src="%sleaflet.min.js" integrity="%s" crossorigin="anonymous"></script>'
        % (_LEAFLET, _LEAFLET_JS_SRI),
        "<script>%s</script>" % _HARITA_JS.replace("__VERI__", _js(veri)),
        "</body></html>"])


# --------------------------------------------------------------------------- #
# Üç boyutlu arazi (three.js)                                                  #
# --------------------------------------------------------------------------- #
_THREE = "https://cdn.jsdelivr.net/npm/three@0.160.0/"
_KALDIRMA_M = 0.5        # çizgi ile yüzey aynı derinliğe düşünce titreşir (z-fighting)
_SIKLIK_M = 5.0
_AZAMI_NOKTA = 4000      # dev parselde dosya şişmesin


def _yuzey_kotu(z: Sequence[Sequence[float]], fi: float, fj: float) -> float:
    """Kesirli (satır, sütun) konumunda ÜÇGENLENMİŞ yüzeyin kotu.

    Tarayıcıdaki ağ her hücreyi (i,j+1)–(i+1,j) köşegeniyle iki üçgene böler. Çift
    doğrusal ara değer hücre ortasında bu yüzeyden |z00+z11−z01−z10|/4 kadar ayrılır;
    engebeli arazide kaba ızgarada bu metreleri bulur ve sınır çizgisi yer yer
    zeminin altında kalır. Çizgi, çizilen yüzeyin kendisinden örneklenir.
    """
    ny, nx = len(z), len(z[0])
    i = min(max(int(math.floor(fi)), 0), ny - 2)
    j = min(max(int(math.floor(fj)), 0), nx - 2)
    v, u = min(max(fi - i, 0.0), 1.0), min(max(fj - j, 0.0), 1.0)
    a, b, c, d = z[i][j], z[i][j + 1], z[i + 1][j], z[i + 1][j + 1]
    if u + v <= 1.0:
        return a + u * (b - a) + v * (c - a)
    return d + (1.0 - u) * (c - d) + (1.0 - v) * (b - d)


def _ortulu_cizgiler(parsel: Dict[str, Any], cerceve: Dict[str, float],
                     zr: Sequence[Sequence[float]]) -> List[List[float]]:
    """Parsel halkalarını araziye örter: [x, y, z, x, y, z, …] dizileri (yerel metre).

    Kenarlar sıklaştırılır, çünkü iki köşe arasındaki düz doğru tepeyi deler,
    vadinin üstünde asılı kalır. Izgaranın dışına çıkan bölüm çizilmez; komşu
    parsel kutudan taşıyorsa çizgi orada kesilir.
    """
    ny, nx = len(zr), len(zr[0])
    cikti: List[List[float]] = []
    for cokgen in parsel["cokgenler"]:
        for halka in cokgen:
            yerel = [((b - cerceve["b0"]) * cerceve["mb"], (cerceve["e0"] - e) * cerceve["me"])
                     for b, e in halka]
            cevre = sum(math.dist(yerel[k], yerel[(k + 1) % len(yerel)]) for k in range(len(yerel)))
            adim = max(min(_SIKLIK_M, max(cerceve["hucre"] / 2.0, 0.5)), cevre / _AZAMI_NOKTA)
            kosu: List[float] = []
            for k in range(len(yerel) + 1):
                (x0, s0), (x1, s1) = yerel[k % len(yerel)], yerel[(k + 1) % len(yerel)]
                if k == len(yerel):
                    parca = 1                      # halkayı kapatan son nokta
                    x1, s1 = x0, s0
                else:
                    parca = max(1, int(math.ceil(math.hypot(x1 - x0, s1 - s0) / adim)))
                for t in range(parca):
                    x, s = x0 + (x1 - x0) * t / parca, s0 + (s1 - s0) * t / parca
                    fj = x / cerceve["hx"] + (nx - 1) / 2.0
                    fi = s / cerceve["hy"] + (ny - 1) / 2.0
                    if 0.0 <= fi <= ny - 1 and 0.0 <= fj <= nx - 1:
                        kosu += [round(x, 1), round(_yuzey_kotu(zr, fi, fj) + _KALDIRMA_M, 2), round(s, 1)]
                    elif kosu:
                        if len(kosu) >= 6:
                            cikti.append(kosu)
                        kosu = []
            if len(kosu) >= 6:
                cikti.append(kosu)
    return cikti


_UCBOYUT_JS = """import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {Line2} from 'three/addons/lines/Line2.js';
import {LineMaterial} from 'three/addons/lines/LineMaterial.js';
import {LineGeometry} from 'three/addons/lines/LineGeometry.js';
const V=__VERI__;
const kap=document.getElementById('sahne'),bekle=document.getElementById('bekle');
let cizici;
try{cizici=new THREE.WebGLRenderer({antialias:true});}
catch(e){bekle.textContent='Tarayıcı WebGL başlatamadı; 3B görünüm gösterilemiyor.';throw e;}
cizici.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
kap.appendChild(cizici.domElement);
const sahne=new THREE.Scene();sahne.background=new THREE.Color(0xeaf0f4);
const R=Math.max(V.gx,V.gz),nx=V.nx,ny=V.ny;
const kamera=new THREE.PerspectiveCamera(38,1,R/500,R*30);
sahne.add(new THREE.HemisphereLight(0xffffff,0x8a8f99,2.2));
const gunes=new THREE.DirectionalLight(0xffffff,2.4);gunes.position.set(-R,R*1.1,-R*0.6);sahne.add(gunes);

// Arazi: x doğu, y yukarı, z güney. Satır 0 kuzeydir; kotlar z0'a göre desimetre tam sayıdır.
const yer=new Float32Array(nx*ny*3),renk=new Float32Array(nx*ny*3);
const basamak=[[0,'#3d7d4c'],[0.3,'#9dc26a'],[0.55,'#e6d98c'],[0.8,'#b98b5d'],[1,'#f3efe8']]
.map(function(b){return [b[0],new THREE.Color(b[1])];});
const zmax=Math.max.apply(null,V.dz)/10||1,c=new THREE.Color();
for(let i=0;i<ny;i++)for(let j=0;j<nx;j++){
const n=i*nx+j,y=V.dz[n]/10;
yer[n*3]=(j-(nx-1)/2)*V.hx;yer[n*3+1]=y;yer[n*3+2]=(i-(ny-1)/2)*V.hy;
const t=y/zmax;let k=1;while(k<basamak.length-1&&t>basamak[k][0])k++;
c.copy(basamak[k-1][1]).lerp(basamak[k][1],(t-basamak[k-1][0])/(basamak[k][0]-basamak[k-1][0]));
renk[n*3]=c.r;renk[n*3+1]=c.g;renk[n*3+2]=c.b;}
// Köşegen (i,j+1)-(i+1,j): sınır çizgisinin kotu Python'da AYNI üçgenlerden örneklendi.
const dizin=[];
for(let i=0;i<ny-1;i++)for(let j=0;j<nx-1;j++){const a=i*nx+j,b=a+1,cc=a+nx,d=cc+1;dizin.push(a,cc,b,b,cc,d);}
const geo=new THREE.BufferGeometry();
geo.setAttribute('position',new THREE.BufferAttribute(yer,3));
geo.setAttribute('color',new THREE.BufferAttribute(renk,3));
geo.setIndex(dizin);geo.computeVertexNormals();
const arazi=new THREE.Group();sahne.add(arazi);
arazi.add(new THREE.Mesh(geo,new THREE.MeshLambertMaterial({vertexColors:true,side:THREE.DoubleSide,
polygonOffset:true,polygonOffsetFactor:1,polygonOffsetUnits:1})));
V.komsular.forEach(function(k){const g=new THREE.BufferGeometry();
g.setAttribute('position',new THREE.BufferAttribute(new Float32Array(k),3));
arazi.add(new THREE.Line(g,new THREE.LineBasicMaterial({color:0x4a4a4a})));});
const kalin=new LineMaterial({color:0xd81b60,linewidth:4});
V.parsel.forEach(function(k){const g=new LineGeometry();g.setPositions(k);arazi.add(new Line2(g,kalin));});

function yazi(metin,renkli){const t=document.createElement('canvas'),b=t.getContext('2d');
b.font='bold 44px Arial';t.width=Math.ceil(b.measureText(metin).width)+28;t.height=64;
b.font='bold 44px Arial';b.fillStyle='rgba(255,255,255,.9)';b.fillRect(0,0,t.width,t.height);
b.strokeStyle=renkli;b.lineWidth=4;b.strokeRect(2,2,t.width-4,t.height-4);
b.fillStyle='#111';b.textBaseline='middle';b.fillText(metin,14,34);
const s=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(t),depthTest:false}));
s.center.set(0.5,0);s.scale.set(R*0.042*t.width/t.height,R*0.042,1);s.renderOrder=5;sahne.add(s);return s;}
const etiket=yazi(V.ad,'#d81b60'),kuzey=yazi('K','#111');
// Kuzey oku yere yatık bir biçimdir: sahneyle birlikte döner, hep gerçek kuzeyi gösterir.
const bicim=new THREE.Shape();bicim.moveTo(0,0.5);bicim.lineTo(0.3,-0.5);bicim.lineTo(0,-0.26);bicim.lineTo(-0.3,-0.5);
const ok=new THREE.Mesh(new THREE.ShapeGeometry(bicim),new THREE.MeshBasicMaterial({color:0x222222,side:THREE.DoubleSide}));
ok.rotation.x=-Math.PI/2;ok.scale.set(R*0.12,R*0.12,1);ok.position.set(V.gx/2+R*0.09,0,-V.gz/2+R*0.1);sahne.add(ok);
const kilavuz=new THREE.Line(new THREE.BufferGeometry().setAttribute('position',
new THREE.BufferAttribute(new Float32Array(6),3)),new THREE.LineBasicMaterial({color:0xd81b60}));sahne.add(kilavuz);

const denet=new OrbitControls(kamera,cizici.domElement);
denet.maxPolarAngle=Math.PI/2-0.02;denet.minDistance=R*0.05;denet.maxDistance=R*6;
function ciz(){cizici.render(sahne,kamera);}
function abart(k){arazi.scale.y=k;
const ey=V.etiket[1]*k;etiket.position.set(V.etiket[0],ey+R*0.07,V.etiket[2]);
kilavuz.geometry.attributes.position.array.set([V.etiket[0],ey,V.etiket[2],V.etiket[0],ey+R*0.07,V.etiket[2]]);
kilavuz.geometry.attributes.position.needsUpdate=true;
ok.position.y=V.kuzey_y*k;
kuzey.position.set(ok.position.x,ok.position.y+R*0.01,ok.position.z-R*0.1);
denet.target.y=hedefY(k);denet.update();
document.getElementById('kat').textContent=String(k).replace('.',',')+'×';ciz();}
function boyut(){const w=kap.clientWidth,h=kap.clientHeight;cizici.setSize(w,h);
kamera.aspect=w/h;kamera.updateProjectionMatrix();kalin.resolution.set(w,h);ciz();}
// Kadraj ABARTILMIŞ arazinin sınır küresine göre kurulur: dik yamaçta 5× abartı araziyi
// genişliğinden yüksek yapar; sabit bir uzaklık onu ekranın dışına taşırdı.
function hedefY(k){return (zmax*0.3+V.etiket[1]*0.4)*k;}
function kadraj(k){const dar=Math.max(1,1.5*kap.clientHeight/Math.max(kap.clientWidth,1));
const d=Math.sqrt(V.gx*V.gx+V.gz*V.gz+zmax*k*zmax*k)*1.15*dar;
denet.target.set(V.etiket[0]*0.4,hedefY(k),V.etiket[2]*0.4);
kamera.position.set(denet.target.x+d*0.23,denet.target.y+d*0.52,denet.target.z+d*0.82);denet.update();}
denet.addEventListener('change',ciz);window.addEventListener('resize',boyut);
const sur=document.getElementById('abarti');
sur.addEventListener('input',function(){abart(parseFloat(sur.value));});
bekle.style.display='none';boyut();kadraj(parseFloat(sur.value));abart(parseFloat(sur.value));"""


def arazi_3d_html(parsel: Dict[str, Any], komsular: Optional[List[Dict[str, Any]]],
                  izgara: Dict[str, Any], ozet: Dict[str, Any]) -> str:
    """Tek dosyalık 3B arazi: ızgaradan ağ, araziye örtülmüş parsel sınırı, düşey abartı sürgüsü.

    Saf işlevdir; three.js dışında ağ çağrısı içermez. Kotlar en düşük kota göre
    desimetre tam sayı olarak gömülür: "1034.5" altı karakter, "345" üç.
    """
    enlemler, boylamlar, z = izgara["enlemler"], izgara["boylamlar"], izgara["z"]
    ny, nx = len(enlemler), len(boylamlar)
    if ny < 2 or nx < 2:
        raise HaritaHatasi("Izgara en az 2×2 olmalı.")
    e0, b0 = (enlemler[0] + enlemler[-1]) / 2.0, (boylamlar[0] + boylamlar[-1]) / 2.0
    mb, me = metre_derece(e0, b0)
    gx, gz = (boylamlar[-1] - boylamlar[0]) * mb, (enlemler[0] - enlemler[-1]) * me
    hx, hy = gx / (nx - 1), gz / (ny - 1)
    taban = min(min(s) for s in z)
    dz = [[int(round((v - taban) * 10.0)) for v in s] for s in z]
    zr = [[v / 10.0 for v in s] for s in dz]          # çizgi, tarayıcının göreceği yuvarlanmış yüzeye oturur
    cerceve = {"e0": e0, "b0": b0, "mb": mb, "me": me, "hx": hx, "hy": hy, "hucre": min(hx, hy)}

    eb, ee = _etiket_noktasi(parsel)
    ex, es = (eb - b0) * mb, (e0 - ee) * me
    ey = _yuzey_kotu(zr, es / hy + (ny - 1) / 2.0, ex / hx + (nx - 1) / 2.0)
    veri = {
        "nx": nx, "ny": ny, "gx": round(gx, 1), "gz": round(gz, 1), "hx": round(hx, 3), "hy": round(hy, 3),
        "dz": [v for s in dz for v in s],
        "parsel": _ortulu_cizgiler(parsel, cerceve, zr),
        "komsular": [c for k in (komsular or []) if k.get("ref") != parsel.get("ref")
                     for c in _ortulu_cizgiler(k, cerceve, zr)],
        "etiket": [round(ex, 1), round(ey, 1), round(es, 1)],
        "kuzey_y": round(_yuzey_kotu(zr, 0.0, nx - 1.0), 1),
        "ad": _ada_parsel(parsel),
    }
    olcu = olc(parsel)
    satirlar = _kart_satirlari(parsel, olcu) + [
        ("Kot (min – maks)", "%s – %s m" % (tr_bicim(ozet["min_kot_m"], 1), tr_bicim(ozet["max_kot_m"], 1))),
        ("Ortalama kot", "%s m" % tr_bicim(ozet["ortalama_kot_m"], 1)),
        ("Kot farkı", "%s m" % tr_bicim(ozet["kot_farki_m"], 1)),
        ("Ortalama eğim", "%% %s" % tr_bicim(ozet["ortalama_egim_yuzde"], 1)),
        ("Hâkim bakı", str(ozet["hakim_baki"])),
    ]
    kart = ('<div class="kart"><h1>%s</h1>%s'
            '<label class="sur">Düşey abartı <input id="abarti" type="range" min="1" max="5" step="0.5" '
            'value="2"> <b id="kat">2×</b></label>'
            "<p>Renkler görelidir: yeşil %s m → açık %s m (görünen alanın en alçak ve en yüksek kotu).</p>"
            "<p>%s</p><p>%s</p><p>Yükseklik: %s — %s</p></div>"
            % (_h(etiket(parsel)), _tablo(satirlar), _h(tr_bicim(taban, 1)),
               _h(tr_bicim(max(max(s) for s in z), 1)), _h(ozet.get("not", "")),
               "<br>".join(_h(u) for u in UYARI), _h(izgara.get("kaynak", "Copernicus DEM")),
               _h(izgara.get("atif", _ATIF % "WorldDEM-30"))))
    importmap = {"imports": {"three": _THREE + "build/three.module.min.js",
                             "three/addons/": _THREE + "examples/jsm/"}}
    return "\n".join([
        "<!DOCTYPE html>", '<html lang="tr"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>3B arazi — %s</title>" % _h(etiket(parsel)),
        "<style>%s\n#sahne{position:absolute;inset:0;background:#eaf0f4}#sahne canvas{display:block}"
        "#bekle{position:absolute;left:0;right:0;top:48%%;text-align:center;color:#555;font-size:13px}"
        ".sur{display:block;margin-top:8px}.sur input{vertical-align:middle;width:130px}</style>" % _ORTAK_CSS,
        '</head><body><div id="sahne"></div>',
        '<div id="bekle">3B görünüm yükleniyor… (three.js CDN\'den gelir; çevrimdışıyken görünmez)</div>',
        kart,
        '<script type="importmap">%s</script>' % _js(importmap),
        '<script type="module">%s</script>' % _UCBOYUT_JS.replace("__VERI__", _js(veri)),
        "</body></html>"])
