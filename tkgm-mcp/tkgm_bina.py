"""tkgm_bina — parsel çevresindeki binaların anlık, TAHMİNİ kütle modeli (LoD1).

Tasarım kuralları:

- Kütle görüntüden çıkarılmaz. Google görüntüsünden (uydu ya da 45°) bina izi ya da 3B
  model türetilmez; Google'ın fotogerçekçi 3B karoları Türkiye'yi zaten kapsamaz
  (2026-09 ölçümü).
- Uydu görüntüsü (kullanıcının kendi Esri anahtarıyla) yalnız gösterilir: 3B sahnede
  zemin dokusu olarak serilir, üzerinde otomatik çıkarım yapılmaz.

Kütle açık bina tabanından (Overture Maps: OpenStreetMap ve Microsoft ML) ve kat
sayısından kurala göre üretilir. Her sonuç "tahmini" etiketi taşır.

Ağ sınırı: yalnız iki adres. `stac.overturemaps.org/catalog.json` güncel sürüm
adını verir. Sürümler ~60 gün tutulur, sonra eski adres 404 döner. Bina
karoları Overture'ın resmî PMTiles arşivinden HTTP Range ile alınır: parsel
başına ~1 karo, ~300 KB.

Saklama: dizinler ve karolar yalnız bellekte tutulur. Bina verisi diske yazılmaz
ve parsel kayıtlarına bağlanıp saklanmaz. 3B sayfa kaynak atfını taşır.

Biçimler elle çözülür, çünkü arka uç yalnız standart kütüphane kullanır:
PMTiles v3 başlığı ve dizinleri (varint, Hilbert karo numarası) ile Mapbox
Vector Tile (protobuf).
"""

from __future__ import annotations

import gzip
import json
import math
import os
import re
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, OrderedDict
from http.client import HTTPException
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import tkgm_geo as geo

Nokta = Tuple[float, float]
# (url, başlangıç, uzunluk) → bayt; uzunluk None ise dosyanın tamamı. Test ağ yerine bunu verir.
Getirici = Callable[[str, int, Optional[int]], bytes]

KATALOG = "https://stac.overturemaps.org/catalog.json"
ARSIV_KOKU = "https://overturemaps-extras-us-west-2.s3.us-west-2.amazonaws.com/tiles/"
KULLANICI_AJANI = "arthurlegal-tkgm/0.6 (+https://github.com/beerbottle90/arthurlegal-mcp)"
ZAMAN_ASIMI = 20.0

KAT_YUKSEKLIGI_M = 3.0
VARSAYILAN_KAT = 3
AZAMI_KAT = 80
_AZAMI_KARO = 4               # 2×2 z14 karosu ≈ 3,6 km; parsel sahnesi bunu aşmaz
_AZAMI_BINA = 1500            # sahne ve dosya boyu için; parsele en yakınlar kalır
_ONBELLEK_SINIRI = 24 * 1024 * 1024
_SURUM_OMRU_SN = 6 * 3600

_KILIT = threading.Lock()     # HTTP taşıması ThreadingHTTPServer'dır
_ONBELLEK: "OrderedDict[Tuple[str, int, Optional[int]], bytes]" = OrderedDict()
_ONBELLEK_BAYT = [0]
_SAYAC = {"istek": 0, "bayt": 0}
_SURUM: Dict[str, Any] = {"ad": None, "zaman": 0.0}
_ARSIVLER: Dict[str, "Arsiv"] = {}


class BinaHatasi(Exception):
    """Bina verisi alınamadı. Çağıran 3B sahneyi binasız üretir; arazi yine çizilir."""


class _ArsivYok(BinaHatasi):
    """Sürüm kaldırılmış (404). Katalog yeniden okunur."""


# --------------------------------------------------------------------------- #
# Ağ — yalnız Overture kataloğu ve PMTiles kovası                              #
# --------------------------------------------------------------------------- #
def ag_istatistigi() -> Dict[str, int]:
    with _KILIT:
        return {"istek": _SAYAC["istek"], "bayt": _SAYAC["bayt"], "onbellek_bayt": _ONBELLEK_BAYT[0]}


def onbellegi_bosalt() -> None:
    with _KILIT:
        _ONBELLEK.clear()
        _ONBELLEK_BAYT[0] = 0
        _ARSIVLER.clear()
        _SURUM.update({"ad": None, "zaman": 0.0})


def _izinli(url: str) -> bool:
    return url == KATALOG or url.startswith(ARSIV_KOKU)


def _http(url: str, bas: int, uzunluk: Optional[int]) -> bytes:
    if not _izinli(url):
        raise BinaHatasi("İzin verilmeyen adres: bu modül yalnız Overture kataloğuna ve bina arşivine bağlanır.")
    anahtar = (url, bas, uzunluk)
    with _KILIT:
        veri = _ONBELLEK.get(anahtar)
        if veri is not None:
            _ONBELLEK.move_to_end(anahtar)
            return veri
    basliklar = {"User-Agent": KULLANICI_AJANI}
    if uzunluk is not None:
        basliklar["Range"] = "bytes=%d-%d" % (bas, bas + uzunluk - 1)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=basliklar),
                                    timeout=ZAMAN_ASIMI) as yanit:
            durum, govde = yanit.status, yanit.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):     # S3 silinmiş nesnede 403 de döndürebilir
            raise _ArsivYok("Overture arşivi bulunamadı (HTTP %d)." % exc.code) from exc
        raise BinaHatasi("Overture sunucusu HTTP %d döndürdü." % exc.code) from exc
    except (urllib.error.URLError, socket.timeout, HTTPException, OSError) as exc:
        raise BinaHatasi("Bina verisine ulaşılamadı (%s). 3B sahne binasız üretilir." % exc) from exc
    if uzunluk is not None and durum != 206:
        # 200 = sunucu Range'i yok saydı ve 180 GB'lık arşivin tamamını yolluyor demektir.
        raise BinaHatasi("Sunucu bayt aralığı isteğini desteklemedi (HTTP %d)." % durum)
    with _KILIT:
        _SAYAC["istek"] += 1
        _SAYAC["bayt"] += len(govde)
        if url != KATALOG:            # katalog sürüm ömrüyle yönetilir, bayt önbelleğine girmez
            _ONBELLEK[anahtar] = govde
            _ONBELLEK_BAYT[0] += len(govde)
            while _ONBELLEK_BAYT[0] > _ONBELLEK_SINIRI and _ONBELLEK:
                _, eski = _ONBELLEK.popitem(last=False)
                _ONBELLEK_BAYT[0] -= len(eski)
    return govde


def guncel_surum(al: Optional[Getirici] = None, yenile: bool = False) -> str:
    """Overture'ın en son yayımı ("2026-08-19.0"). TKGM_OVERTURE_SURUM sabitler."""
    sabit = (os.environ.get("TKGM_OVERTURE_SURUM") or "").strip()
    if sabit:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", sabit):
            raise BinaHatasi("TKGM_OVERTURE_SURUM biçimi YYYY-AA-GG.N olmalı (ör. 2026-08-19.0).")
        return sabit
    with _KILIT:
        if not yenile and _SURUM["ad"] and time.time() - _SURUM["zaman"] < _SURUM_OMRU_SN:
            return _SURUM["ad"]
    try:
        katalog = json.loads((al or _http)(KATALOG, 0, None).decode("utf-8"))
    except ValueError as exc:
        raise BinaHatasi("Overture kataloğu okunamadı.") from exc
    ad = katalog.get("latest") if isinstance(katalog, dict) else None
    if not isinstance(ad, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", ad):
        raise BinaHatasi("Overture kataloğunda geçerli bir 'latest' sürümü yok.")
    with _KILIT:
        _SURUM.update({"ad": ad, "zaman": time.time()})
    return ad


# --------------------------------------------------------------------------- #
# PMTiles v3                                                                   #
# --------------------------------------------------------------------------- #
_BASLIK = struct.Struct("<7sB11Q6B4iB2i")     # 127 bayt
_BASLIK_ALANLARI = ("sihir", "surum", "kok_ofset", "kok_boy", "meta_ofset", "meta_boy", "yaprak_ofset",
                    "yaprak_boy", "veri_ofset", "veri_boy", "adresli", "kayit", "icerik", "kumeli",
                    "ic_sikistirma", "karo_sikistirma", "karo_tipi", "min_zoom", "max_zoom",
                    "min_boylam", "min_enlem", "max_boylam", "max_enlem", "merkez_zoom",
                    "merkez_boylam", "merkez_enlem")


def _varint(b: bytes, i: int) -> Tuple[int, int]:
    sonuc, kay = 0, 0
    while True:
        if i >= len(b):
            raise BinaHatasi("Bozuk veri: varint yarıda kesildi.")
        c = b[i]
        i += 1
        sonuc |= (c & 0x7F) << kay
        if c < 0x80:
            return sonuc, i
        kay += 7
        if kay > 63:
            raise BinaHatasi("Bozuk veri: varint çok uzun.")


def _ac(ham: bytes, tur: int) -> bytes:
    """PMTiles sıkıştırması: 1 yok, 2 gzip. Brotli/zstd standart kütüphanede yok."""
    if tur in (0, 1):
        return ham
    if tur == 2:
        try:
            return gzip.decompress(ham)
        except (OSError, EOFError) as exc:
            raise BinaHatasi("Bozuk gzip akışı.") from exc
    raise BinaHatasi("Arşiv sıkıştırması %d standart kütüphaneyle açılamaz." % tur)


def baslik_coz(b: bytes) -> Dict[str, Any]:
    if len(b) < 127 or b[:7] != b"PMTiles":
        raise BinaHatasi("PMTiles başlığı yok.")
    h = dict(zip(_BASLIK_ALANLARI, _BASLIK.unpack(b[:127])))
    if h["surum"] != 3:
        raise BinaHatasi("Yalnız PMTiles v3 okunur (gelen v%d)." % h["surum"])
    return h


def dizin_coz(b: bytes) -> List[Tuple[int, int, int, int]]:
    """(karo_no, tekrar, uzunluk, ofset) kayıtları. Tekrar 0 = yaprak dizine işaret."""
    n, i = _varint(b, 0)
    nolar, tekrar, boy, ofset = [], [], [], []
    son = 0
    for _ in range(n):
        v, i = _varint(b, i)
        son += v
        nolar.append(son)
    for dizi in (tekrar, boy):
        for _ in range(n):
            v, i = _varint(b, i)
            dizi.append(v)
    for k in range(n):
        v, i = _varint(b, i)
        # 0 = "bir öncekinin hemen ardından": arşiv karoları ardışık yazıldığında ofset tekrarlanmaz.
        ofset.append(ofset[k - 1] + boy[k - 1] if v == 0 and k > 0 else v - 1)
    return list(zip(nolar, tekrar, boy, ofset))


def karo_no(z: int, x: int, y: int) -> int:
    """PMTiles karo numarası: düşük zoomların toplam karosu + Hilbert eğrisindeki sıra."""
    taban = ((1 << (2 * z)) - 1) // 3
    n, d, s = 1 << z, 0, (1 << z) >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = n - 1 - x, n - 1 - y
            x, y = y, x
        s >>= 1
    return taban + d


def _kayit_bul(kayitlar: Sequence[Tuple[int, int, int, int]], no: int) -> Optional[Tuple[int, int, int, int]]:
    alt, ust = 0, len(kayitlar) - 1
    while alt <= ust:
        orta = (alt + ust) // 2
        if kayitlar[orta][0] < no:
            alt = orta + 1
        elif kayitlar[orta][0] > no:
            ust = orta - 1
        else:
            return kayitlar[orta]
    if ust >= 0:
        k = kayitlar[ust]
        if k[1] == 0 or no - k[0] < k[1]:
            return k
    return None


class Arsiv:
    """Uzak PMTiles arşivi. Başlık ve kök dizin ilk 16 KB'ta gelir; gerisi Range ile."""

    def __init__(self, url: str, al: Optional[Getirici] = None) -> None:
        self.url, self.al = url, (al or _http)
        ilk = self.al(url, 0, 16384)
        self.h = baslik_coz(ilk)
        if self.h["karo_tipi"] != 1:
            raise BinaHatasi("Arşiv vektör karo (MVT) değil.")
        o, n = self.h["kok_ofset"], self.h["kok_boy"]
        ham = ilk[o:o + n] if o + n <= len(ilk) else self.al(url, o, n)
        self.kok = dizin_coz(_ac(ham, self.h["ic_sikistirma"]))
        self._yapraklar: Dict[Tuple[int, int], List[Tuple[int, int, int, int]]] = {}

    def karo(self, z: int, x: int, y: int) -> Optional[bytes]:
        no, kayitlar = karo_no(z, x, y), self.kok
        for _ in range(4):
            k = _kayit_bul(kayitlar, no)
            if k is None:
                return None
            _, tekrar, boy, ofset = k
            if tekrar > 0:
                return _ac(self.al(self.url, self.h["veri_ofset"] + ofset, boy), self.h["karo_sikistirma"])
            if (ofset, boy) not in self._yapraklar:
                ham = self.al(self.url, self.h["yaprak_ofset"] + ofset, boy)
                self._yapraklar[(ofset, boy)] = dizin_coz(_ac(ham, self.h["ic_sikistirma"]))
            kayitlar = self._yapraklar[(ofset, boy)]
        raise BinaHatasi("PMTiles dizin derinliği aşıldı.")


# --------------------------------------------------------------------------- #
# Mapbox Vector Tile (protobuf)                                                #
# --------------------------------------------------------------------------- #
def _alanlar(b: bytes):
    """(alan_no, değer) üretir; değer varint için int, diğerleri için bayt dilimi."""
    i, son = 0, len(b)
    while i < son:
        anahtar, i = _varint(b, i)
        no, tel = anahtar >> 3, anahtar & 7
        if tel == 0:
            v, i = _varint(b, i)
        elif tel == 1:
            v, i = b[i:i + 8], i + 8
        elif tel == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif tel == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise BinaHatasi("Bozuk karo: protobuf tel tipi %d." % tel)
        if i > son:
            raise BinaHatasi("Bozuk karo: alan karo sonunu aşıyor.")
        yield no, v


def _paketli(b: bytes) -> List[int]:
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i)
        out.append(v)
    return out


def _zz(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _deger(b: bytes) -> Any:
    for no, v in _alanlar(b):
        if no == 1:
            return bytes(v).decode("utf-8", "replace")
        if no == 2:
            return struct.unpack("<f", v)[0]
        if no == 3:
            return struct.unpack("<d", v)[0]
        if no == 4:
            return v - (1 << 64) if v >= 1 << 63 else v
        if no == 5:
            return v
        if no == 6:
            return _zz(v)
        if no == 7:
            return bool(v)
    return None


def _halkalar(komut: List[int]) -> List[List[Tuple[int, int]]]:
    halkalar, halka, x, y, i = [], [], 0, 0, 0
    while i < len(komut):
        k, adet = komut[i] & 7, komut[i] >> 3
        i += 1
        if k == 7:                     # ClosePath
            if halka:
                halkalar.append(halka)
            halka = []
            continue
        for _ in range(adet):
            if i + 1 >= len(komut):
                raise BinaHatasi("Bozuk karo: geometri yarıda kesildi.")
            x += _zz(komut[i])
            y += _zz(komut[i + 1])
            i += 2
            if k == 1 and halka:       # MoveTo yeni halka başlatır
                halkalar.append(halka)
                halka = []
            halka.append((x, y))
    if halka:
        halkalar.append(halka)
    return halkalar


def mvt_coz(b: bytes, katman_adi: str = "building") -> Tuple[int, List[Dict[str, Any]]]:
    """Tek katmanın (uzunluk birimi, nesneler). Nesne: {"tip", "oz", "halkalar"} (karo pikseli)."""
    for no, katman in _alanlar(b):
        if no != 3:
            continue
        ad, anahtarlar, degerler, ham, olcek = None, [], [], [], 4096
        for ln, lv in _alanlar(katman):
            if ln == 1:
                ad = bytes(lv).decode("utf-8", "replace")
            elif ln == 2:
                ham.append(lv)
            elif ln == 3:
                anahtarlar.append(bytes(lv).decode("utf-8", "replace"))
            elif ln == 4:
                degerler.append(_deger(lv))
            elif ln == 5:
                olcek = lv
        if ad != katman_adi:
            continue
        nesneler = []
        for fb in ham:
            oz, tip, geom = {}, 0, []
            for fn, fv in _alanlar(fb):
                if fn == 2:
                    t = _paketli(fv)
                    for k in range(0, len(t) - 1, 2):
                        if t[k] < len(anahtarlar) and t[k + 1] < len(degerler):
                            oz[anahtarlar[t[k]]] = degerler[t[k + 1]]
                elif fn == 3:
                    tip = fv
                elif fn == 4:
                    geom = _paketli(fv)
            nesneler.append({"tip": tip, "oz": oz, "halkalar": _halkalar(geom)})
        return olcek, nesneler
    return 4096, []


def _alan(h: Sequence[Tuple[float, float]]) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(h, list(h[1:]) + [h[0]])) / 2.0


def _cokgenlere_ayir(halkalar: List[List[Tuple[int, int]]]) -> List[List[List[Tuple[int, int]]]]:
    """MVT 2.1 §4.3.4.4: karo koordinatında (y aşağı) alanı POZİTİF halka dış halkadır;
    ardından gelen negatifler onun delikleridir."""
    out: List[List[List[Tuple[int, int]]]] = []
    for h in halkalar:
        if len(h) < 3:
            continue
        a = _alan(h)
        if a > 0:
            out.append([h])
        elif a < 0 and out:
            out[-1].append(h)
    return out


# --------------------------------------------------------------------------- #
# Karo ↔ coğrafi                                                               #
# --------------------------------------------------------------------------- #
def karo_xy(boylam: float, enlem: float, z: int) -> Tuple[int, int]:
    n = 1 << z
    x = int((boylam + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(enlem))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def _piksel_cografi(z: int, tx: int, ty: int, px: float, py: float, olcek: int) -> Nokta:
    n = float(1 << z)
    boylam = (tx + px / olcek) / n * 360.0 - 180.0
    enlem = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (ty + py / olcek) / n))))
    return boylam, enlem


def _sayi(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


# --------------------------------------------------------------------------- #
# Bina tabanları                                                               #
# --------------------------------------------------------------------------- #
def _arsiv(url: str, al: Optional[Getirici]) -> Arsiv:
    if al is not None:
        return Arsiv(url, al)
    with _KILIT:
        a = _ARSIVLER.get(url)
    if a is None:
        a = Arsiv(url)
        with _KILIT:
            _ARSIVLER[url] = a
    return a


def binalar(bati: float, guney: float, dogu: float, kuzey: float,
            al: Optional[Getirici] = None) -> Dict[str, Any]:
    """Kutudaki bina tabanları. Merkezi kutuda kalan her bina bir kez döner.

    Karo sınırındaki bina her iki karoda da (kırpılmış) bulunur; kimliği aynıdır.
    Tek parça saklanır: alanı büyük olan. Kırpılmış küçük parça atılır.
    """
    if not (-180.0 <= bati < dogu <= 180.0 and -85.0 <= guney < kuzey <= 85.0):
        raise BinaHatasi("Geçersiz kutu (sıra: batı, güney, doğu, kuzey; derece).")
    onceki = ag_istatistigi()
    for deneme in range(2):
        surum = guncel_surum(al, yenile=deneme > 0)
        url = "%s%s/buildings.pmtiles" % (ARSIV_KOKU, surum)
        try:
            arsiv = _arsiv(url, al)
            sonuc = _kutudan(arsiv, bati, guney, dogu, kuzey)
            break
        except _ArsivYok:
            with _KILIT:
                _ARSIVLER.pop(url, None)
            if deneme or os.environ.get("TKGM_OVERTURE_SURUM"):
                raise BinaHatasi("Overture bina arşivi %s artık yayında değil." % surum)
    sonuc["surum"] = surum
    simdi = ag_istatistigi()
    sonuc["indirilen_bayt"] = simdi["bayt"] - onceki["bayt"]
    return sonuc


def _kutudan(arsiv: Arsiv, bati: float, guney: float, dogu: float, kuzey: float) -> Dict[str, Any]:
    z = arsiv.h["max_zoom"]
    x0, y0 = karo_xy(bati, kuzey, z)
    x1, y1 = karo_xy(dogu, guney, z)
    if (x1 - x0 + 1) * (y1 - y0 + 1) > _AZAMI_KARO:
        raise BinaHatasi("Kutu parsel ölçeğini aşıyor (%d karo). Binalar yalnız parsel çevresi için alınır."
                         % ((x1 - x0 + 1) * (y1 - y0 + 1)))
    secilen: Dict[Any, Dict[str, Any]] = {}
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            ham = arsiv.karo(z, tx, ty)
            if not ham:
                continue
            olcek, nesneler = mvt_coz(ham)
            for sira, n in enumerate(nesneler):
                oz = n["oz"]
                if n["tip"] != 3 or oz.get("is_underground") is True:
                    continue
                cokgenler = _cokgenlere_ayir(n["halkalar"])
                if not cokgenler:
                    continue
                en_buyuk = max(cokgenler, key=lambda c: _alan(c[0]))[0]
                mx, my = geo.agirlik_merkezi(en_buyuk)
                merkez = _piksel_cografi(z, tx, ty, mx, my, olcek)
                if not (bati <= merkez[0] <= dogu and guney <= merkez[1] <= kuzey):
                    continue
                alan = sum(_alan(c[0]) for c in cokgenler)
                kimlik = oz.get("id") or (tx, ty, sira)
                if kimlik in secilen and secilen[kimlik]["_alan"] >= alan:
                    continue
                kat = _sayi(oz.get("num_floors"))
                secilen[kimlik] = {
                    "_alan": alan,
                    "merkez": (round(merkez[0], 7), round(merkez[1], 7)),
                    "cokgenler": [[[tuple(round(v, 7) for v in _piksel_cografi(z, tx, ty, px, py, olcek))
                                    for px, py in h] for h in c] for c in cokgenler],
                    "yukseklik_m": _sayi(oz.get("height")),
                    "kat": int(kat) if kat and kat < AZAMI_KAT else None,
                    "kaynak": str(oz.get("@geometry_source") or "bilinmiyor"),
                }
    liste = list(secilen.values())
    for b in liste:
        del b["_alan"]
    kirpildi = 0
    if len(liste) > _AZAMI_BINA:
        mb, me = (dogu + bati) / 2.0, (kuzey + guney) / 2.0
        kosin = math.cos(math.radians(me))
        liste.sort(key=lambda b: ((b["merkez"][0] - mb) * kosin) ** 2 + (b["merkez"][1] - me) ** 2)
        kirpildi = len(liste) - _AZAMI_BINA
        liste = liste[:_AZAMI_BINA]
    return {"binalar": liste, "karo": (x1 - x0 + 1) * (y1 - y0 + 1), "kirpilan": kirpildi,
            "kaynaklar": dict(Counter(b["kaynak"] for b in liste).most_common())}


def atif(kaynaklar: Dict[str, int], surum: str) -> str:
    """Kaynak atfı ve lisans adları; yalnız sahnede gerçekten bulunan kaynaklar."""
    parca = []
    if "OpenStreetMap" in kaynaklar:
        parca.append("© OpenStreetMap katkıcıları (ODbL 1.0)")
    if any("Microsoft" in k for k in kaynaklar):
        parca.append("Microsoft ML Buildings (CDLA-Permissive-2.0)")
    if any("Google" in k for k in kaynaklar):
        parca.append("Google Open Buildings (CC BY 4.0)")
    digerleri = sorted(k for k in kaynaklar if k != "OpenStreetMap" and "Microsoft" not in k
                       and "Google" not in k and k != "bilinmiyor")
    parca += digerleri
    parca.append("Overture Maps Foundation (%s)" % surum)
    return "Bina tabanları: " + ", ".join(parca)


# --------------------------------------------------------------------------- #
# Kat sayısı — tapu niteliğinden                                               #
# --------------------------------------------------------------------------- #
_BIRLER = {"bir": 1, "tek": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6, "yedi": 7,
           "sekiz": 8, "dokuz": 9}
_ONLAR = {"on": 10, "yirmi": 20, "otuz": 30, "kırk": 40}
_SAYI_KALIBI = (r"(\d{1,2}|(?:(?:on|yirmi|otuz|kırk)\s*)?(?:bir|tek|iki|üç|dört|beş|altı|yedi|sekiz|dokuz)"
                r"|on|yirmi|otuz|kırk)")
_ZEMIN_ARTI = re.compile(r"zemin\s*(?:kat[ıi]?)?\s*\+\s*" + _SAYI_KALIBI + r"\b")
_NORMAL = re.compile(r"\b" + _SAYI_KALIBI + r"\s*(?:adet\s+)?normal\s+kat")
_KATLI = re.compile(r"\b" + _SAYI_KALIBI + r"\s*(?:\(\s*\d+\s*\)\s*)?kat(?:l[ıi]|\b)")


def _tr_kucuk(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def _sayiya(s: str) -> Optional[int]:
    s = s.strip()
    if s.isdigit():
        return int(s)
    toplam = 0
    for parca in s.split():
        if parca in _ONLAR:
            toplam += _ONLAR[parca]
        elif parca in _BIRLER:
            toplam += _BIRLER[parca]
        else:
            # "onbir" gibi bitişik yazım
            on = next((o for o in _ONLAR if parca.startswith(o) and parca[len(o):] in _BIRLER), None)
            if on is None:
                return None
            toplam += _ONLAR[on] + _BIRLER[parca[len(on):]]
    return toplam or None


def kat_cozumle(nitelik: Any) -> Optional[Tuple[int, str]]:
    """Tapu niteliğinden zemin üstü kat sayısı: (kat, eşleşen ifade) ya da None.

    "6 Katlı Betonarme Apartman" → 6; "Altı Katlı Kargir" → 6; "Zemin+4 Katlı" → 5;
    "Bodrum + Zemin + 3 Normal Kat" → 4 (bodrum yer altıdır, sayılmaz). Kat sayısı
    geçmeyen nitelikte ("Betonarme Apartman", "Arsa") None döner; tahmin edilmez.
    """
    if not isinstance(nitelik, str) or not nitelik.strip():
        return None
    t = _tr_kucuk(" ".join(nitelik.split()))
    for kalip, ek in ((_ZEMIN_ARTI, 1), (_NORMAL, None), (_KATLI, 0)):
        m = kalip.search(t)
        if not m:
            continue
        n = _sayiya(m.group(1))
        if n is None:
            continue
        if ek is None:                # "N normal kat": zemin kat anılıyorsa o da sayılır
            ek = 1 if "zemin" in t else 0
        kat = n + ek
        if 1 <= kat <= AZAMI_KAT:
            return kat, nitelik.strip()[:80]
    return None


# --------------------------------------------------------------------------- #
# Kütle modeli                                                                 #
# --------------------------------------------------------------------------- #
def _tam_sayi(v: Any, ad: str) -> int:
    # bool int'in alt türüdür: `kat: true` 1 kat sayılmasın. "6" (metin) kabul, "6,5" değil.
    if isinstance(v, bool) or isinstance(v, float) and not v.is_integer():
        raise BinaHatasi("%s bir tam sayı olmalı." % ad)
    try:
        return int(v)
    except (TypeError, ValueError) as exc:
        raise BinaHatasi("%s bir tam sayı olmalı." % ad) from exc


def girdi_denetle(kat: Any = None, varsayilan_kat: Any = VARSAYILAN_KAT) -> Tuple[Optional[int], int]:
    """(parsel katı ya da None, varsayılan kat). Ağa çıkmadan önce çağrılır: yanlış girdi
    indirmeden önce, açık bir hatayla reddedilsin."""
    parsel_kati = None
    if kat not in (None, ""):
        parsel_kati = _tam_sayi(kat, "kat (zemin üstü kat sayısı)")
        if not 1 <= parsel_kati <= AZAMI_KAT:
            raise BinaHatasi("kat 1 ile %d arasında olmalı." % AZAMI_KAT)
    varsayilan = _tam_sayi(VARSAYILAN_KAT if varsayilan_kat in (None, "") else varsayilan_kat, "varsayilan_kat")
    if not 1 <= varsayilan <= AZAMI_KAT:
        raise BinaHatasi("varsayilan_kat 1 ile %d arasında olmalı." % AZAMI_KAT)
    return parsel_kati, varsayilan


def kutle_modeli(parsel: Dict[str, Any], veri: Dict[str, Any], kat: Any = None,
                 varsayilan_kat: int = VARSAYILAN_KAT) -> Dict[str, Any]:
    """Her binaya yükseklik ve dayanak verir; kural tablosu:

    1. Parseldeki bina (merkezi parsel içinde): kullanıcının verdiği kat, yoksa tapu
       niteliğindeki kat (× 3 m).
    2. Bu ikisi yoksa sahne "açık veri" kipindedir: OSM/Overture `height`, yoksa
       `num_floors` × 3 m, yoksa varsayılan kat.
    3. Kullanıcı ya da nitelik yüksekliği varsa açık verideki yükseklikler bu sahnede
       KULLANILMAZ; komşular varsayılan kattır. İki kaynağın yükseklikleri aynı katmanda
       karıştırılmaz; Türkiye'de binaların ~%1'inde yükseklik olduğundan kayıp önemsizdir.
    """
    parsel_kati, varsayilan = girdi_denetle(kat, varsayilan_kat)
    dayanak, aciklama = None, None
    if parsel_kati:
        dayanak, aciklama = "kullanici", "kullanıcı girdisi"
    else:
        cozum = kat_cozumle((parsel.get("oznitelik") or {}).get("nitelik"))
        if cozum:
            parsel_kati, ifade = cozum
            dayanak, aciklama = "nitelik", "tapu niteliği: '%s'" % ifade
    kip = "kullanici" if parsel_kati else "acik_veri"

    cikti, sayac = [], Counter()
    for b in veri.get("binalar") or []:
        parselde = any(geo.cokgen_icinde(b["merkez"], c) for c in parsel["cokgenler"])
        if parselde and parsel_kati:
            h, d = parsel_kati * KAT_YUKSEKLIGI_M, dayanak
        elif kip == "acik_veri" and b.get("yukseklik_m"):
            h, d = min(b["yukseklik_m"], AZAMI_KAT * KAT_YUKSEKLIGI_M), "veri"
        elif kip == "acik_veri" and b.get("kat"):
            h, d = b["kat"] * KAT_YUKSEKLIGI_M, "veri"
        else:
            h, d = varsayilan * KAT_YUKSEKLIGI_M, "varsayim"
        sayac[d] += 1
        cikti.append({"cokgenler": b["cokgenler"], "merkez": b["merkez"], "h": round(h, 1),
                      "parselde": parselde, "dayanak": d})

    parseldekiler = [b for b in cikti if b["parselde"]]
    notlar = ["TAHMİNİDİR: taban izi açık veriden, yükseklik kat × %s m kuralından üretilmiştir. "
              "Ölçüm, imar durumu, ruhsat ya da röleve yerine geçmez." % ("%g" % KAT_YUKSEKLIGI_M).replace(".", ",")]
    if kip == "kullanici":
        notlar.append("Parsel binasının yüksekliği %s dayanağıyla verildi. Bu sahnede açık verideki komşu "
                      "yükseklikleri kullanılmadı (iki kaynak aynı katmanda karıştırılmaz); komşular "
                      "varsayılan %d kattır." % (aciklama, varsayilan))
    if parsel_kati and not parseldekiler:
        notlar.append("Parselde açık veride bina izi yok; %d kat bilgisi tabana uygulanamadı." % parsel_kati)
    if sayac["varsayim"]:
        notlar.append("%d binada yükseklik bilgisi yok; varsayılan %d kat (≈%s m) çizildi."
                      % (sayac["varsayim"], varsayilan,
                         ("%g" % (varsayilan * KAT_YUKSEKLIGI_M)).replace(".", ",")))
    if veri.get("kirpilan"):
        notlar.append("Sahne %d binayla sınırlandı; parsele uzak %d bina çizilmedi."
                      % (len(cikti), veri["kirpilan"]))
    ozet: Dict[str, Any] = {
        "kip": kip, "bina_sayisi": len(cikti),
        "dayanak_sayilari": dict(sayac),
        "parsel_binasi": ({"sayi": len(parseldekiler), "kat": parsel_kati,
                           "yukseklik_m": round(parsel_kati * KAT_YUKSEKLIGI_M, 1) if parsel_kati else
                           (max(b["h"] for b in parseldekiler) if parseldekiler else None),
                           "dayanak": aciklama or ("açık veri/varsayım" if parseldekiler else None)}
                          if parseldekiler or parsel_kati else None),
        "notlar": notlar,
        "atif": atif(veri.get("kaynaklar") or {}, veri.get("surum") or "?"),
    }
    return {"binalar": cikti, "ozet": ozet}
