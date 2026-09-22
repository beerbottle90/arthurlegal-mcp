"""tkgm_canli — TKGM Parsel Sorgu'ya canlı, hız sınırlı bağlantı; yer adı için OSM Nominatim.

Kullanıcı sohbette "Kadıköy Caferağa 123 ada 45 parsel" ya da bir yer adı söyler; bu modül
parseli TKGM Parsel Sorgu'nun herkese açık verisinden getirir ve tkgm_parsel'in okuduğu
GeoJSON'u döndürür. Ölçü, kroki, harita ve hukuk köprüsü sonra yerelde çalışır.

Tasarım (docs/MANIFESTO.md'nin teknik karşılığı). Hedef: TKGM'ye tek bir tarayıcı
sekmesinden fazla yük bindirmemek.

- Tek kapı. Bütün TKGM istekleri `Kapi.getir`den geçer; başka ağ yolu yoktur. Adres sabit
  şablondan kurulur, yol parçaları yalnız doğrulanmış sayıdır: kullanıcı adres veremez.
- Tek sıra. TKGM'de aynı anda en çok BİR istek bulunur; iki isteğin başlangıcı arasında en az
  ARALIK_SN (2 sn) vardır. Üst sınır dakikada 60 / 2 = 30 istektir, bütün kullanıcıların toplamı.
- Taşan istek TKGM'ye gitmez. Sıradaki bekleme KUYRUK_SN'yi (20 sn) aşacaksa istek burada
  "yoğun" yanıtıyla döner; sıra en çok 20 / 2 = 10 istek tutar.
- Yavaşlayınca geri çekilir. Yanıt YAVAS_SN'yi (2 sn) aşınca aralık ikiye katlanır (en çok
  16 sn, dakikada ~4); art arda 20 hızlı yanıttan sonra yarıya iner.
- Devre kesici. 429/503: en az 60 sn hiç istek yok (Retry-After'a uyulur), tekrarında 15 dk'ya
  kadar katlanır; üst üste üç ağ/sunucu hatası da aynı. 401/403: 24 saat duruş. Engel
  aşılmaya çalışılmaz: kimlik, adres ya da IP değiştirilmez.
- Önce önbellek. Parsel 24 sa, "bulunamadı" 1 sa, il/ilçe/mahalle listeleri 30 gün.
  Ada ve parsel LİSTELEME uçları hiç çağrılmaz.
- Toplu taramaya kapalı. Çağrı başına tek parsel; aynı adada 10 dk'da 12'den fazla ayrı parsel,
  ardışık altı parsel numarası, bir mahallede saatte 40 parsel, ~1 km²'lik bir hücrede saatte 20
  nokta durdurulur. Günlük toplam GUNLUK_AZAMI (3.000) istek.
- Açık kimlik. User-Agent "ArthurLegal-Tapu/<sürüm> (+manifesto)". Çerez, Referer, tarayıcı
  taklidi yok.
- Tek anahtar. TKGM_CANLI=0 bütün canlı istekleri kapatır.

Sınırlar süreç başınadır. Birleşik uç (Fly) birden çok makinede çalışırsa TKGM_MAKINE_SAYISI
aralığı çarpar, günlük tavanı böler: toplam yine dakikada 30'dur. Fly'da sayı verilmezse 2 alınır.
"""

from __future__ import annotations

import json
import math
import os
import re
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SURUM = "0.5.0"      # server.py __version__ ile aynı tutulur (test denetler)
MANIFESTO = "https://github.com/beerbottle90/arthurlegal-mcp/blob/master/tkgm-mcp/docs/MANIFESTO.md"
ILETISIM = "https://www.linkedin.com/in/ertug-demir-arthurlegal/"


def _ortam(ad: str, varsayilan: float, alt: float, ust: float) -> float:
    try:
        x = float(os.environ.get(ad, "") or varsayilan)
    except ValueError:
        return varsayilan
    return min(max(x, alt), ust) if math.isfinite(x) else varsayilan


def _makine_sayisi() -> int:
    """Sınırlar süreç başınadır; paylaşılan uç birden çok makinede çalışırsa bütçe makinelere
    bölünür. Fly'da sayı bildirilmemişse ihtiyatla 2 alınır (arthurlegal-mcp iki makinededir)."""
    deger = os.environ.get("TKGM_MAKINE_SAYISI", "").strip()
    if not deger:
        return 2 if os.environ.get("FLY_APP_NAME") else 1
    try:
        return min(max(int(deger), 1), 10)
    except ValueError:
        return 2 if os.environ.get("FLY_APP_NAME") else 1


MAKINE = _makine_sayisi()
# Ortamdan en çok gevşetilebilecek hâl, makine başına dakikada 60 istektir (alt sınır 1 sn).
ARALIK_SN = _ortam("TKGM_ARALIK_SN", 2.0, 1.0, 60.0) * MAKINE
KUYRUK_SN = _ortam("TKGM_KUYRUK_SN", 20.0, 2.0, 60.0)
GUNLUK_AZAMI = max(1, int(_ortam("TKGM_GUNLUK_AZAMI", 3000, 1, 20000)) // MAKINE)
ZAMAN_ASIMI = _ortam("TKGM_ZAMAN_ASIMI", 10.0, 2.0, 30.0)
YAVAS_SN = 2.0
AZAMI_ARALIK_SN = 16.0
CAGRI_BUTCESI_SN = 45.0          # bir araç çağrısının sırada ve ağda geçirebileceği toplam süre

PARSEL_OMRU = 24 * 3600.0
YOK_OMRU = 3600.0
LISTE_OMRU = 30 * 24 * 3600.0
YER_OMRU = 24 * 3600.0           # anahtar kullanıcının yazdığı metindir: bellekte bir günden uzun kalmaz
_AZAMI_YANIT = 8_000_000

# Uç şablonları: (kök, yol). {} yerine yalnız _parca()'dan geçmiş sayı girer.
UCLAR = {
    "il": ("web", "app/modules/administrativeQuery/data/ilListe.json"),
    "ilce": ("api", "idariYapi/ilceListe/{}"),
    "mahalle": ("api", "idariYapi/mahalleListe/{}"),
    "parsel": ("api", "parsel/{}/{}/{}"),
    "konum": ("api", "parsel/{}/{}/"),
}
# Canlı yanıtla sınanan uçlar (23.09.2026, 5 istek; yanıt 0,09-0,31 sn). Bir uç değişir de
# buradan çıkarılırsa durum() onu "doğrulanmamış" diye sayar.
DOGRULANAN = {"il", "ilce", "mahalle", "parsel", "konum"}

_KOKLER = {
    "api": ("TKGM_API_URL", "https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api/"),
    "web": ("TKGM_WEB_URL", "https://parselsorgu.tkgm.gov.tr/"),
    "nominatim": ("TKGM_NOMINATIM_URL", "https://nominatim.openstreetmap.org/"),
}

# Türkiye kutusu; tkgm_baglanti ve server ile aynı sınır.
TR_ENLEM = (35.0, 43.0)
TR_BOYLAM = (25.0, 45.5)


def kimlik() -> str:
    """İsteklerin taşıdığı ad. Sunucu kayıtlarını okuyan biri kim olduğumuzu ve bu tasarımı
    buradan bulur; ASCII, çünkü HTTP başlığı latin-1'dir."""
    return "ArthurLegal-Tapu/%s (+%s; acik kaynak, ticari degil)" % (SURUM, MANIFESTO)


def _kok(tur: str) -> str:
    ad, varsayilan = _KOKLER[tur]
    kok = (os.environ.get(ad) or varsayilan).strip()
    return kok if kok.endswith("/") else kok + "/"


# --------------------------------------------------------------------------- #
# Hatalar: hepsi kullanıcıya olduğu gibi gösterilebilecek Türkçe metin taşır.   #
# --------------------------------------------------------------------------- #
class CanliHata(Exception):
    """Canlı sorgu yapılamadı; ileti kullanıcıya gösterilebilir."""


class Yogun(CanliHata):
    """Sıra dolu: istek TKGM'ye hiç gitmedi."""

    def __init__(self, bekleme: float, ad: str = "TKGM") -> None:
        self.bekleme = bekleme
        super().__init__("%s sırası dolu: bekleme ~%d sn olurdu. İstek %s'ye gönderilmedi; biraz sonra "
                         "yeniden deneyin." % (ad, math.ceil(bekleme), ad))


class Kapali(CanliHata):
    """Devre açık, günlük tavan dolu ya da canlı sorgu kapalı."""

    def __init__(self, ileti: str, kalan: float = 0.0) -> None:
        self.kalan = kalan
        super().__init__(ileti)


class Bulunamadi(CanliHata):
    """TKGM kayıt döndürmedi ya da ad eşleşmedi."""


class BilgiEksik(CanliHata):
    """Sorgu için gereken il/ilçe/mahalle/ada/parsel bilgisi eksik."""


class Taranamaz(CanliHata):
    """Toplu tarama deseni: bağlayıcı tek tek sorgu için tasarlandı."""


class Belirsiz(CanliHata):
    """Ad birden çok kayda uyuyor; kullanıcıya sorulmalı."""

    def __init__(self, tur: str, aranan: str, adaylar: List[Dict[str, Any]]) -> None:
        self.tur, self.aranan, self.adaylar = tur, aranan, adaylar[:8]
        self.ek: Dict[str, Any] = {}      # metinden çözülmüş ada/parsel: seçimden sonra yinelemek için
        super().__init__("'%s' için birden çok %s eşleşti: %s." % (
            aranan, tur, ", ".join("%s (id %s)" % (a["ad"], a["id"]) for a in self.adaylar)))

    def yanit(self) -> Dict[str, Any]:
        alan = {"il": "il", "ilçe": "ilce", "mahalle": "mahalle_id"}.get(self.tur, self.tur)
        out = {"belirsiz": self.tur, "aranan": self.aranan, "adaylar": self.adaylar,
               "talimat": "Kullanıcıya hangisi olduğunu sorun; mahalle için `mahalle_id` ile, il/ilçe için "
                          "adın tam yazımıyla yineleyin (alan: %s)." % alan}
        out.update(self.ek)
        return out


def _sure_metni(sn: float) -> str:
    sn = max(1, int(math.ceil(sn)))
    if sn < 120:
        return "%d sn" % sn
    if sn < 7200:
        return "%d dk" % math.ceil(sn / 60.0)
    return "%.1f saat" % (sn / 3600.0)


# --------------------------------------------------------------------------- #
# Sıra: aynı anda tek istek, başlangıçlar arası en az `aralik` sn.              #
# --------------------------------------------------------------------------- #
class Sinirlayici:
    def __init__(self, aralik: float, kuyruk_sn: float, azami_aralik: float = AZAMI_ARALIK_SN,
                 yavas_sn: float = YAVAS_SN, ad: str = "TKGM") -> None:
        self.taban = float(aralik)
        self.aralik = float(aralik)
        self.azami = max(float(azami_aralik), self.taban)
        self.kuyruk_sn = float(kuyruk_sn)
        self.yavas_sn = float(yavas_sn)
        self.ad = ad
        self._kosul = threading.Condition(threading.Lock())
        self._sonraki = float("-inf")     # bir sonraki isteğin en erken başlangıcı
        self._mesgul = False              # karşı tarafta bizden bir istek var mı
        self._bekleyen = 0
        self._hizli = 0

    def _tahmin(self, simdi: float) -> float:
        return max(0.0, self._sonraki - simdi) + self._bekleyen * self.aralik

    def gir(self, son: Optional[float] = None) -> None:
        """Sıra gelince döner. Bekleme sınırı aşılacaksa hemen Yogun: istek hiç gönderilmez."""
        with self._kosul:
            simdi = time.monotonic()
            sinir = simdi + self.kuyruk_sn
            if son is not None:
                sinir = min(sinir, son)
            tahmin = self._tahmin(simdi)
            if simdi + tahmin > sinir:
                raise Yogun(tahmin, self.ad)
            self._bekleyen += 1
            try:
                while True:
                    simdi = time.monotonic()
                    if not self._mesgul and simdi >= self._sonraki:
                        break
                    if simdi >= sinir:
                        raise Yogun(self._tahmin(simdi), self.ad)
                    bekle = sinir - simdi
                    if not self._mesgul:
                        bekle = min(bekle, self._sonraki - simdi)
                    self._kosul.wait(max(bekle, 0.001))
                self._mesgul = True
                self._sonraki = simdi + self.aralik
            finally:
                self._bekleyen -= 1

    def cik(self, sure: Optional[float]) -> None:
        """sure: karşı tarafın yanıt süresi (sn); None = istek gönderilmedi."""
        with self._kosul:
            self._mesgul = False
            if sure is not None:
                if sure > self.yavas_sn:
                    self.aralik = min(self.azami, self.aralik * 2.0)
                    self._hizli = 0
                elif sure <= self.yavas_sn / 2.0 and self.aralik > self.taban:
                    self._hizli += 1
                    if self._hizli >= 20:
                        self.aralik = max(self.taban, self.aralik / 2.0)
                        self._hizli = 0
            self._kosul.notify_all()

    def dakikada(self) -> int:
        return int(60.0 // self.aralik)


# --------------------------------------------------------------------------- #
# Devre kesici                                                                 #
# --------------------------------------------------------------------------- #
class Devre:
    TABAN_SN = 60.0
    TAVAN_SN = 900.0
    RET_SN = 24 * 3600.0
    ESIK = 3

    def __init__(self, saat: Callable[[], float] = time.monotonic) -> None:
        self._saat = saat
        self._kilit = threading.Lock()
        self._bitis = 0.0
        self._neden = ""
        self._hata = 0
        self._kademe = 0

    def kalan(self) -> float:
        with self._kilit:
            return max(0.0, self._bitis - self._saat())

    def denetle(self, ad: str) -> None:
        kalan = self.kalan()
        if kalan > 0:
            raise Kapali("%s şu an istek almıyor (%s). Bağlayıcı %s bekliyor; bu sürede %s'ye hiç istek "
                         "gönderilmez. Sonra yeniden deneyin." % (ad, self._neden, _sure_metni(kalan), ad), kalan)

    def basari(self) -> None:
        with self._kilit:
            self._hata = 0
            self._kademe = max(0, self._kademe - 1)

    def _ac(self, sure: float, neden: str) -> None:
        self._bitis = max(self._bitis, self._saat() + sure)
        self._neden = neden

    def _geri_cekil(self) -> float:
        self._kademe = min(self._kademe + 1, 5)
        return min(self.TAVAN_SN, self.TABAN_SN * 2 ** (self._kademe - 1))

    def yavasla(self, retry_after: Optional[float], neden: str) -> None:
        with self._kilit:
            sure = self._geri_cekil()
            if retry_after:
                sure = max(sure, min(float(retry_after), 6 * 3600.0))
            self._ac(sure, neden)

    def hata(self, neden: str) -> None:
        with self._kilit:
            self._hata += 1
            if self._hata >= self.ESIK:
                self._hata = 0
                self._ac(self._geri_cekil(), neden)

    def reddedildi(self, neden: str) -> None:
        with self._kilit:
            self._ac(self.RET_SN, neden)

    def durum(self) -> Dict[str, Any]:
        kalan = self.kalan()
        if kalan <= 0:
            return {"durum": "çalışıyor"}
        return {"durum": "duraklatıldı", "kalan": _sure_metni(kalan), "neden": self._neden}


class GunlukSayac:
    """Türkiye saatiyle gün başına istek tavanı."""

    def __init__(self, azami: int) -> None:
        self.azami = int(azami)
        self._kilit = threading.Lock()
        self._gun = ""
        self.sayi = 0

    @staticmethod
    def _bugun() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))

    def _yenile(self) -> None:
        gun = self._bugun()
        if gun != self._gun:
            self._gun, self.sayi = gun, 0

    def denetle(self, ad: str) -> None:
        with self._kilit:
            self._yenile()
            if self.sayi >= self.azami:
                raise Kapali("Bugünkü %s istek tavanı (%d) doldu; canlı sorgu yarın açılır. Önbellekteki "
                             "parseller sorgulanabilir." % (ad, self.azami))

    def say(self, ad: str) -> None:
        with self._kilit:
            self._yenile()
            if self.sayi >= self.azami:
                raise Kapali("Bugünkü %s istek tavanı (%d) doldu." % (ad, self.azami))
            self.sayi += 1


# --------------------------------------------------------------------------- #
# Ağ                                                                           #
# --------------------------------------------------------------------------- #
class _Yonlendirme(Exception):
    pass


class _YonlendirmeYok(urllib.request.HTTPRedirectHandler):
    """Yönlendirme izlenmez: sabit adresten başka bir yere (giriş sayfası, başka alan adı)
    gidilmez; yönlendirme hata sayılır."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise _Yonlendirme("HTTP %s yönlendirmesi" % code)


_ACICI = urllib.request.build_opener(_YonlendirmeYok)


def _retry_after(basliklar: Any) -> Optional[float]:
    try:
        deger = (basliklar.get("Retry-After") or "").strip()
    except AttributeError:
        return None
    if not deger:
        return None
    if deger.isdigit():
        return float(deger)
    try:
        return max(0.0, parsedate_to_datetime(deger).timestamp() - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


class Kapi:
    """Bir dış servise giden tek yol: açık mı, devre, günlük tavan, sıra, istek."""

    def __init__(self, ad: str, aralik: float, kuyruk_sn: float, gunluk: int,
                 anahtar: str = "TKGM_CANLI", azami_aralik: float = AZAMI_ARALIK_SN,
                 zaman_asimi: Optional[float] = None) -> None:
        self.ad = ad
        self.sinir = Sinirlayici(aralik, kuyruk_sn, azami_aralik=azami_aralik, ad=ad)
        self.devre = Devre()
        self.gunluk = GunlukSayac(gunluk)
        self.anahtar = anahtar
        self.zaman_asimi = zaman_asimi or ZAMAN_ASIMI
        self.gonderilen = 0
        self._kilit = threading.Lock()

    def acik(self) -> bool:
        return os.environ.get(self.anahtar, "1").strip().lower() not in (
            "0", "false", "hayir", "hayır", "kapali", "kapalı", "off")

    def getir(self, url: str, son: Optional[float] = None) -> Any:
        if not self.acik():
            raise Kapali("%s canlı sorgusu bu kurulumda kapalı (%s=0). Parsel dosyası varsa parsel_oku ile "
                         "çalışın." % (self.ad, self.anahtar))
        self.devre.denetle(self.ad)
        self.gunluk.denetle(self.ad)
        self.sinir.gir(son)
        sure: Optional[float] = None
        try:
            # Sırada beklerken devre açılmış ya da tavan dolmuş olabilir.
            self.devre.denetle(self.ad)
            self.gunluk.say(self.ad)
            with self._kilit:
                self.gonderilen += 1
            t0 = time.monotonic()
            try:
                return self._http(url)
            finally:
                sure = time.monotonic() - t0
        finally:
            self.sinir.cik(sure)

    def _http(self, url: str) -> Any:
        istek = urllib.request.Request(url, headers={
            "User-Agent": kimlik(), "Accept": "application/json", "Accept-Language": "tr"})
        try:
            with _ACICI.open(istek, timeout=self.zaman_asimi) as yanit:
                govde = yanit.read(_AZAMI_YANIT + 1)
                kod = getattr(yanit, "status", 200)
        except _Yonlendirme as exc:
            self.devre.hata("yönlendirme")
            raise CanliHata("%s yanıt yerine yönlendirme döndürdü (%s); izlenmedi. Bakım ya da giriş sayfası "
                            "olabilir." % (self.ad, exc)) from None
        except urllib.error.HTTPError as exc:
            kod = exc.code
            if kod in (404, 410):
                self.devre.basari()
                raise Bulunamadi("%s bu sorgu için kayıt döndürmedi (HTTP %d)." % (self.ad, kod)) from None
            if kod in (401, 403):
                self.devre.reddedildi("HTTP %d: erişim reddedildi" % kod)
                raise Kapali("%s erişimi reddetti (HTTP %d). Bağlayıcı 24 saat duruyor; engel aşılmaya "
                             "çalışılmaz." % (self.ad, kod), Devre.RET_SN) from None
            if kod in (429, 503):
                self.devre.yavasla(_retry_after(exc.headers), "HTTP %d: yavaşlama isteği" % kod)
                raise Kapali("%s yavaşlamamızı istedi (HTTP %d). Bağlayıcı %s bekliyor; bu sürede istek "
                             "gönderilmez." % (self.ad, kod, _sure_metni(self.devre.kalan())),
                             self.devre.kalan()) from None
            if kod == 400:
                raise CanliHata("%s isteği geçersiz saydı (HTTP 400): numaraları kontrol edin." % self.ad) from None
            self.devre.hata("HTTP %d" % kod)
            raise CanliHata("%s sunucu hatası döndürdü (HTTP %d)." % (self.ad, kod)) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, HTTPException, OSError) as exc:
            self.devre.hata("ulaşılamadı")
            raise CanliHata("%s'ye ulaşılamadı (%s)." % (self.ad, getattr(exc, "reason", exc))) from None
        if kod == 204:
            raise Bulunamadi("%s bu sorgu için kayıt döndürmedi." % self.ad)
        if len(govde) > _AZAMI_YANIT:
            self.devre.hata("yanıt çok büyük")
            raise CanliHata("%s yanıtı beklenenden büyük; işlenmedi." % self.ad)
        try:
            veri = json.loads(govde.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            self.devre.hata("JSON olmayan yanıt")
            raise CanliHata("%s JSON olmayan bir yanıt döndürdü (bakım ya da erişim sayfası olabilir)."
                            % self.ad) from None
        self.devre.basari()
        return veri


TKGM = Kapi("TKGM", ARALIK_SN, KUYRUK_SN, GUNLUK_AZAMI, anahtar="TKGM_CANLI",
            azami_aralik=AZAMI_ARALIK_SN * MAKINE)
# Nominatim kullanım koşulu en çok saniyede bir istek; bütün makinelerin toplamı 1,1 sn aralıkla o
# sınırın altında kalır.
OSM = Kapi("OpenStreetMap Nominatim", 1.1 * MAKINE, 10.0 + 1.1 * MAKINE, max(1, 2000 // MAKINE),
           anahtar="TKGM_CANLI", azami_aralik=8.0 * MAKINE)


# --------------------------------------------------------------------------- #
# Önbellek ve tarama koruması                                                  #
# --------------------------------------------------------------------------- #
class Onbellek:
    def __init__(self, sinir: int = 3000, saat: Callable[[], float] = time.monotonic) -> None:
        self.sinir = sinir
        self._saat = saat
        self._kilit = threading.Lock()
        self._d: "OrderedDict[Any, Tuple[Any, float, float]]" = OrderedDict()

    def al(self, anahtar: Any) -> Optional[Tuple[Any, float]]:
        """(değer, yaş sn) ya da None. Değer None ise "bulunamadı" kaydıdır."""
        with self._kilit:
            kayit = self._d.get(anahtar)
            if kayit is None:
                return None
            deger, konuldu, bitis = kayit
            simdi = self._saat()
            if simdi > bitis:
                del self._d[anahtar]
                return None
            self._d.move_to_end(anahtar)
            return deger, simdi - konuldu

    def koy(self, anahtar: Any, deger: Any, omur: float) -> None:
        with self._kilit:
            simdi = self._saat()
            self._d[anahtar] = (deger, simdi, simdi + omur)
            self._d.move_to_end(anahtar)
            while len(self._d) > self.sinir:
                self._d.popitem(last=False)

    def bosalt(self) -> None:
        with self._kilit:
            self._d.clear()

    def __len__(self) -> int:
        with self._kilit:
            return len(self._d)


def _ardisik_uzunluk(kume: set, n: int) -> int:
    alt = 0
    while n - alt - 1 in kume:
        alt += 1
    ust = 0
    while n + ust + 1 in kume:
        ust += 1
    return alt + 1 + ust


class TaramaKorumasi:
    """Kimlikten bağımsız desen denetimi: birleşik uçta istemci kimliği yoktur, bu yüzden
    sınır kişiye değil desene konur. Önbellekten dönen sorgu da sayılır."""

    ADA_PENCERE, ADA_AZAMI = 600.0, 12
    ARDISIK, ARDISIK_CEZA = 6, 3600.0
    MAHALLE_PENCERE, MAHALLE_AZAMI = 3600.0, 40
    HUCRE_PENCERE, HUCRE_AZAMI = 3600.0, 20
    _AZAMI_ANAHTAR = 20000

    def __init__(self, saat: Callable[[], float] = time.monotonic) -> None:
        self._saat = saat
        self._kilit = threading.Lock()
        self._kayit: "OrderedDict[Any, Dict[Any, float]]" = OrderedDict()
        self._ceza: Dict[Any, float] = {}

    def _pencere(self, anahtar: Any, pencere: float, simdi: float) -> Dict[Any, float]:
        d = self._kayit.get(anahtar)
        if d is None:
            d = self._kayit[anahtar] = {}
            while len(self._kayit) > self._AZAMI_ANAHTAR:
                self._kayit.popitem(last=False)
        else:
            self._kayit.move_to_end(anahtar)
            for k in [k for k, t in d.items() if simdi - t > pencere]:
                del d[k]
        return d

    def parsel(self, mahalle_id: int, ada: int, parsel: int) -> None:
        with self._kilit:
            simdi = self._saat()
            ada_anahtari = ("ada", mahalle_id, ada)
            ceza = self._ceza.get(ada_anahtari, 0.0)
            if ceza > simdi:
                raise Taranamaz("Bu adada ardışık parsel taraması görüldü; ada %s süreyle kapalı. Bağlayıcı tek tek "
                                "parsel sorgusu için tasarlandı." % _sure_metni(ceza - simdi))
            ada_d = self._pencere(ada_anahtari, self.ADA_PENCERE, simdi)
            mah_d = self._pencere(("mahalle", mahalle_id), self.MAHALLE_PENCERE, simdi)
            yeni_ada, yeni_mah = parsel not in ada_d, (ada, parsel) not in mah_d
            if yeni_ada and len(ada_d) >= self.ADA_AZAMI:
                raise Taranamaz("Aynı adada 10 dakikada %d ayrı parsel sorgulandı; toplu tarama sayılıyor. Bir süre "
                                "sonra yeniden deneyin." % self.ADA_AZAMI)
            if yeni_mah and len(mah_d) >= self.MAHALLE_AZAMI:
                raise Taranamaz("Aynı mahallede bir saatte %d ayrı parsel sorgulandı; toplu tarama sayılıyor."
                                % self.MAHALLE_AZAMI)
            if yeni_ada and _ardisik_uzunluk(set(ada_d), parsel) >= self.ARDISIK:
                if len(self._ceza) > 1000:
                    self._ceza = {k: t for k, t in self._ceza.items() if t > simdi}
                self._ceza[ada_anahtari] = simdi + self.ARDISIK_CEZA
                raise Taranamaz("Aynı adada %d ardışık parsel numarası: tarama deseni. Bu ada 1 saat kapalı; "
                                "bağlayıcı tek tek parsel sorgusu için tasarlandı." % self.ARDISIK)
            ada_d[parsel] = simdi
            mah_d[(ada, parsel)] = simdi

    def konum(self, enlem: float, boylam: float) -> None:
        with self._kilit:
            simdi = self._saat()
            d = self._pencere(("hucre", math.floor(enlem * 100), math.floor(boylam * 100)),
                              self.HUCRE_PENCERE, simdi)
            nokta = (round(enlem, 5), round(boylam, 5))
            if nokta not in d and len(d) >= self.HUCRE_AZAMI:
                raise Taranamaz("Yaklaşık 1 km²'lik bir alanda bir saatte %d ayrı nokta sorgulandı; ızgara taraması "
                                "sayılıyor." % self.HUCRE_AZAMI)
            d[nokta] = simdi


ONBELLEK = Onbellek()
TARAMA = TaramaKorumasi()


# --------------------------------------------------------------------------- #
# Ad eşleştirme (Türkçe)                                                       #
# --------------------------------------------------------------------------- #
_BUYUK_KUCUK = str.maketrans({"I": "ı", "İ": "i"})
_ASCII = str.maketrans("ıçğöşüâîû", "icgosuaiu")
_EKLER = {"mahallesi", "mahalle", "mah", "mh", "koyu", "koy", "ilcesi", "ili", "belediyesi", "beldesi"}


def _kucuk(s: Any) -> str:
    return unicodedata.normalize("NFC", str(s)).translate(_BUYUK_KUCUK).lower()


def katla_tam(s: Any) -> str:
    """Büyük/küçük harf, Türkçe harf ve noktalama farkını siler; ekleri korur."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", _kucuk(s).translate(_ASCII)).split())


def katla(s: Any) -> str:
    """katla_tam + "mahallesi", "köyü" gibi tür eklerini atar."""
    return " ".join(k for k in katla_tam(s).split() if k not in _EKLER)


def _anahtarlar(ad: str) -> Tuple[str, str]:
    return katla(ad), katla_tam(ad).replace(" ", "")


def baslik(s: str) -> str:
    """TKGM listeleri BÜYÜK harf yazar: "KADIKÖY" → "Kadıköy". Karışık yazım olduğu gibi kalır."""
    s = " ".join(str(s).split())
    if s != s.upper():
        return s
    kelimeler = []
    for k in _kucuk(s).split(" "):
        if k:
            ilk = {"i": "İ", "ı": "I"}.get(k[0], k[0].upper())
            kelimeler.append(ilk + k[1:])
    return " ".join(kelimeler)


def _benzerlik(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def eslestir(ad: Any, kayitlar: Sequence[Dict[str, Any]], tur: str) -> Dict[str, Any]:
    """Tam → önek → bulanık. Tek aday dönmezse Belirsiz ya da Bulunamadi (önerilerle)."""
    ham = str(ad or "").strip()
    if not ham:
        raise BilgiEksik("%s adı gerekli." % tur.capitalize())
    if re.fullmatch(r"\d{1,9}", ham):
        for k in kayitlar:
            if k["id"] == int(ham):
                return k
        raise Bulunamadi("%s kimliği %s listede yok." % (tur.capitalize(), ham))
    h1, h2 = _anahtarlar(ham)
    if not h1 and not h2:
        raise BilgiEksik("%s adı gerekli." % tur.capitalize())
    anahtarli = [(k, _anahtarlar(k["ad"])) for k in kayitlar]
    tam = [k for k, (a1, a2) in anahtarli if (h1 and a1 == h1) or (h2 and a2 == h2)]
    if len(tam) == 1:
        return tam[0]
    if len(tam) > 1:
        raise Belirsiz(tur, ham, tam)
    if len(h1) >= 3:
        onek = [k for k, (a1, _a2) in anahtarli if a1.startswith(h1)
                or (len(a1) >= 4 and h1.startswith(a1) and len(h1) - len(a1) <= 4)]
        if len(onek) == 1:
            return onek[0]
        if 1 < len(onek) <= 8:
            raise Belirsiz(tur, ham, onek)
    puanli = sorted(((_benzerlik(h1, a1), k) for k, (a1, _a2) in anahtarli), key=lambda x: -x[0])
    if puanli and puanli[0][0] >= 0.82 and (len(puanli) == 1 or puanli[0][0] - puanli[1][0] >= 0.06):
        return puanli[0][1]
    yakin = [k for p, k in puanli[:5] if p >= 0.5]
    if len(yakin) > 1 and puanli[0][0] >= 0.82:
        raise Belirsiz(tur, ham, yakin)
    raise Bulunamadi("'%s' adında %s bulunamadı.%s" % (
        ham, tur, (" Yakın adlar: %s." % ", ".join(k["ad"] for k in yakin)) if yakin else ""))


# --------------------------------------------------------------------------- #
# Yanıt ayrıştırma                                                             #
# --------------------------------------------------------------------------- #
_GECERSIZ = re.compile("[\x00-\x1f\x7f\ud800-\udfff]")


def _temiz(v: Any, azami: int = 120) -> str:
    return " ".join(_GECERSIZ.sub(" ", str(v)).split())[:azami]


def _liste(veri: Any) -> List[Dict[str, Any]]:
    """İdari liste → [{"id", "ad"}]. GeoJSON FeatureCollection da düz liste de kabul edilir."""
    if isinstance(veri, dict):
        kayitlar = veri.get("features") or veri.get("data") or veri.get("items") or []
    else:
        kayitlar = veri if isinstance(veri, list) else []
    out: List[Dict[str, Any]] = []
    gorulen = set()
    for k in kayitlar:
        if not isinstance(k, dict):
            continue
        oz = k.get("properties") if isinstance(k.get("properties"), dict) else k
        kucuk = {str(a).lower(): v for a, v in oz.items()}
        kid = next((kucuk[a] for a in ("id", "kimlik", "value") if a in kucuk), None)
        ad = next((kucuk[a] for a in ("text", "ad", "adi", "name", "label") if kucuk.get(a)), None)
        try:
            kid = int(str(kid).strip())
        except (TypeError, ValueError):
            continue
        if ad is None or kid in gorulen:
            continue
        gorulen.add(kid)
        out.append({"id": kid, "ad": baslik(_temiz(ad))})
    return out


def _ozellik(veri: Any) -> Dict[str, Any]:
    """Parsel yanıtı → tek GeoJSON Feature (Polygon/MultiPolygon)."""
    if isinstance(veri, dict) and veri.get("type") == "FeatureCollection":
        adaylar = veri.get("features") or []
    elif isinstance(veri, dict):
        adaylar = [veri]
    elif isinstance(veri, list):
        adaylar = veri
    else:
        adaylar = []
    for f in adaylar:
        geom = f.get("geometry") if isinstance(f, dict) else None
        if isinstance(geom, dict) and geom.get("type") in ("Polygon", "MultiPolygon"):
            oz = f.get("properties") if isinstance(f.get("properties"), dict) else {}
            return {"type": "Feature", "geometry": geom, "properties": oz}
    raise Bulunamadi("TKGM bu sorgu için parsel geometrisi döndürmedi: numara yanlış olabilir ya da parsel "
                     "ifraz/tevhitle kapanmış olabilir.")


def _parca(deger: Any, ad: str, alt: int, ust: int) -> int:
    if isinstance(deger, bool):
        raise BilgiEksik("`%s` sayı olmalı." % ad)
    s = str(deger if deger is not None else "").strip()
    if not re.fullmatch(r"\d{1,9}", s):
        raise BilgiEksik("`%s` pozitif tam sayı olmalı (verilen: %s)." % (ad, s[:20] or "boş"))
    x = int(s)
    if not alt <= x <= ust:
        raise BilgiEksik("`%s` %d ile %d arasında olmalı." % (ad, alt, ust))
    return x


def _adres(uc: str, *parcalar: Any) -> str:
    kok, sablon = UCLAR[uc]
    for p in parcalar:
        if isinstance(p, bool) or not re.fullmatch(r"-?\d{1,9}(\.\d{1,9})?", str(p)):
            raise ValueError("adres parçası sayı değil: %r" % (p,))
    return _kok(kok) + sablon.format(*parcalar)


# --------------------------------------------------------------------------- #
# Sorgular                                                                     #
# --------------------------------------------------------------------------- #
def _liste_getir(anahtar: Tuple, uc: str, parcalar: Tuple, son: Optional[float]) -> List[Dict[str, Any]]:
    kayit = ONBELLEK.al(anahtar)
    if kayit is not None and kayit[0]:
        return kayit[0]
    liste = _liste(TKGM.getir(_adres(uc, *parcalar), son))
    if not liste:
        raise CanliHata("TKGM %s listesi boş ya da tanınmayan biçimde döndü." % uc)
    ONBELLEK.koy(anahtar, liste, LISTE_OMRU)
    return liste


def il_listesi(son: Optional[float] = None) -> List[Dict[str, Any]]:
    return _liste_getir(("il",), "il", (), son)


def ilce_listesi(il_id: int, son: Optional[float] = None) -> List[Dict[str, Any]]:
    return _liste_getir(("ilce", il_id), "ilce", (il_id,), son)


def mahalle_listesi(ilce_id: int, son: Optional[float] = None) -> List[Dict[str, Any]]:
    return _liste_getir(("mahalle", ilce_id), "mahalle", (ilce_id,), son)


def _onbellekteki_ilceler() -> List[Tuple[int, Dict[str, Any]]]:
    """Ağa çıkmadan: önceden getirilmiş ilçe listeleri (il verilmeyen sorgu için)."""
    out = []
    for il in (ONBELLEK.al(("il",)) or ([], 0))[0] or []:
        kayit = ONBELLEK.al(("ilce", il["id"]))
        if kayit and kayit[0]:
            out += [(il["id"], k) for k in kayit[0]]
    return out


def yer_coz(il: Any, ilce: Any, mahalle: Any, son: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Ad → TKGM kimlikleri. İl verilmezse yalnız önbellekteki ilçe listelerinden çıkarılır."""
    if not str(ilce or "").strip() or not str(mahalle or "").strip():
        raise BilgiEksik("İlçe ve mahalle adı gerekli (il de verilirse ilk sorgu hızlanır).")
    if str(il or "").strip():
        il_k = eslestir(il, il_listesi(son), "il")
        ilce_k = eslestir(ilce, ilce_listesi(il_k["id"], son), "ilçe")
    else:
        iller = {k["id"]: k for k in (ONBELLEK.al(("il",)) or ([], 0))[0] or []}
        adaylar = []
        for il_id, k in _onbellekteki_ilceler():
            if katla(k["ad"]) == katla(ilce):
                adaylar.append((il_id, k))
        if len(adaylar) != 1:
            raise BilgiEksik("İl adı gerekli: '%s' ilçesinin ilini de yazın (örn. il=İstanbul)." % ilce)
        il_k, ilce_k = iller[adaylar[0][0]], adaylar[0][1]
    mah_k = eslestir(mahalle, mahalle_listesi(ilce_k["id"], son), "mahalle")
    return {"il": il_k, "ilce": ilce_k, "mahalle": mah_k}


def parsel_getir(mahalle_id: Any, ada: Any, parsel: Any,
                 son: Optional[float] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(GeoJSON Feature, kaynak). Önce tarama koruması, sonra önbellek, sonra TKGM."""
    mahalle_id = _parca(mahalle_id, "mahalle_id", 1, 999_999_999)
    ada = _parca(ada, "ada", 0, 999_999)          # köy parsellerinde ada 0'dır
    parsel = _parca(parsel, "parsel", 1, 999_999)
    TARAMA.parsel(mahalle_id, ada, parsel)
    anahtar = ("parsel", mahalle_id, ada, parsel)
    kayit = ONBELLEK.al(anahtar)
    if kayit is not None:
        if kayit[0] is None:
            raise Bulunamadi("TKGM bu mahallede %d ada %d parseli döndürmedi (son 1 saat içinde sorgulandı)."
                             % (ada, parsel))
        return kayit[0], {"kaynak": "önbellek", "yas_sn": round(kayit[1])}
    try:
        ozellik = _ozellik(TKGM.getir(_adres("parsel", mahalle_id, ada, parsel), son))
    except Bulunamadi:
        ONBELLEK.koy(anahtar, None, YOK_OMRU)
        raise
    ONBELLEK.koy(anahtar, ozellik, PARSEL_OMRU)
    return ozellik, {"kaynak": "canlı", "zaman": time.time()}


def _koordinat(enlem: Any, boylam: Any) -> Tuple[float, float]:
    try:
        e, b = float(enlem), float(boylam)
    except (TypeError, ValueError):
        raise BilgiEksik("`enlem` ve `boylam` sayı olmalı.") from None
    if not (math.isfinite(e) and math.isfinite(b)):
        raise BilgiEksik("`enlem` ve `boylam` sayı olmalı.")
    if not (TR_ENLEM[0] <= e <= TR_ENLEM[1] and TR_BOYLAM[0] <= b <= TR_BOYLAM[1]):
        if TR_ENLEM[0] <= b <= TR_ENLEM[1] and TR_BOYLAM[0] <= e <= TR_BOYLAM[1]:
            raise BilgiEksik("Koordinat ters görünüyor: enlem ~35-43, boylam ~25-45 olmalı.")
        raise BilgiEksik("Koordinat Türkiye sınırları dışında.")
    return e, b


def konum_getir(enlem: Any, boylam: Any, son: Optional[float] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    e, b = _koordinat(enlem, boylam)
    TARAMA.konum(e, b)
    anahtar = ("konum", round(e, 6), round(b, 6))
    kayit = ONBELLEK.al(anahtar)
    if kayit is not None:
        if kayit[0] is None:
            raise Bulunamadi("Bu noktada TKGM parsel döndürmedi (son 1 saat içinde sorgulandı): yol, dere ya da "
                             "tescil dışı alan olabilir.")
        return kayit[0], {"kaynak": "önbellek", "yas_sn": round(kayit[1])}
    try:
        ozellik = _ozellik(TKGM.getir(_adres("konum", "%.6f" % e, "%.6f" % b), son))
    except Bulunamadi:
        ONBELLEK.koy(anahtar, None, YOK_OMRU)
        raise Bulunamadi("Bu noktada TKGM parsel döndürmedi: yol, dere ya da tescil dışı alan olabilir.") from None
    ONBELLEK.koy(anahtar, ozellik, PARSEL_OMRU)
    return ozellik, {"kaynak": "canlı", "zaman": time.time()}


def yer_ara(sorgu: Any, son: Optional[float] = None) -> List[Dict[str, Any]]:
    """Yer adı / adres → en çok beş aday (OpenStreetMap Nominatim, yalnız Türkiye)."""
    q = _temiz(sorgu, 200)
    if len(q) < 2:
        raise BilgiEksik("`sorgu`: yer adı ya da adres, örn. 'Kadıköy Moda Parkı'.")
    anahtar = ("yer", katla_tam(q))
    kayit = ONBELLEK.al(anahtar)
    if kayit is not None and kayit[0] is not None:
        return kayit[0]
    url = _kok("nominatim") + "search?" + urllib.parse.urlencode({
        "q": q, "format": "jsonv2", "addressdetails": 1, "countrycodes": "tr", "limit": 5,
        "accept-language": "tr"})
    veri = OSM.getir(url, son)
    out: List[Dict[str, Any]] = []
    for k in veri if isinstance(veri, list) else []:
        if not isinstance(k, dict):
            continue
        try:
            e, b = float(k.get("lat")), float(k.get("lon"))
        except (TypeError, ValueError):
            continue
        adres = k.get("address") if isinstance(k.get("address"), dict) else {}
        out.append({
            "ad": _temiz(k.get("display_name", ""), 200), "enlem": round(e, 6), "boylam": round(b, 6),
            "tur": _temiz("%s/%s" % (k.get("category") or k.get("class") or "", k.get("type") or ""), 40),
            "il": _temiz(adres.get("province") or adres.get("state") or "", 60),
            "ilce": _temiz(adres.get("town") or adres.get("county") or adres.get("city_district") or "", 60),
            "mahalle": _temiz(adres.get("suburb") or adres.get("quarter") or adres.get("neighbourhood")
                              or adres.get("village") or "", 60)})
    ONBELLEK.koy(anahtar, out, YER_OMRU)
    return out


# --------------------------------------------------------------------------- #
# Serbest metin: "İstanbul Kadıköy Caferağa 123 ada 45 parsel"                  #
# --------------------------------------------------------------------------- #
_ADA_PARSEL = (
    re.compile(r"(\d+)\s*ada\D{0,15}?(\d+)\s*(?:no'?lu\s*|numaral[ıi]\s*|say[ıi]l[ıi]\s*)?parsel"),
    re.compile(r"ada\s*(?:no)?\s*[:.]?\s*(\d+)\D{1,15}parsel\s*(?:no)?\s*[:.]?\s*(\d+)"),
    re.compile(r"(?<![\d.,])(\d{1,6})\s*/\s*(\d{1,6})(?![\d.,])"),
)
_KOORDINAT = re.compile(r"(-?\d{2}[.,]\d{3,})\s*[,;/ ]\s*(-?\d{2}[.,]\d{3,})")
_DOLGU = {katla_tam(k) for k in (
    "ada", "adası", "adanın", "parsel", "parseli", "parselin", "parselini", "no", "nolu", "numaralı", "sayılı",
    "il", "ilçe", "ilçesi", "mahalle", "mahallesi", "mah", "mh", "köy", "köyü", "mevkii", "de", "da", "deki",
    "daki", "te", "ta", "nerede", "nerde", "neresi", "getir", "göster", "bul", "sorgula", "bilgi", "bilgisi",
    "rapor", "raporu", "raporla", "hakkında", "için", "ve", "ile", "bana", "lütfen", "nedir", "ne", "detay",
    "detaylı", "tapu", "tkgm", "hangi", "kim", "var", "mı", "mi", "the")}


def ada_parsel_bul(metin: str) -> Optional[Tuple[int, int, Tuple[int, int]]]:
    k = _kucuk(metin)
    for kalip in _ADA_PARSEL:
        m = kalip.search(k)
        if m:
            return int(m.group(1)), int(m.group(2)), m.span()
    return None


def _kelimeler(metin: str) -> List[str]:
    s = re.sub(r"['’`´]\w*", " ", _kucuk(metin))       # Kadıköy'deki → kadıköy
    s = re.sub(r"\d+", " ", s)
    return [w for w in katla_tam(s).split() if w not in _DOLGU and len(w) > 1]


def _ara(kelimeler: List[str], kayitlar: Sequence[Dict[str, Any]],
         bulanik: bool = False) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Kelime öbeklerinden (en uzundan) listede tek kayda uyanı bulur; kalan kelimeleri döndürür."""
    anahtarli = [(k, _anahtarlar(k["ad"])) for k in kayitlar]
    for n in range(min(4, len(kelimeler)), 0, -1):
        for i in range(len(kelimeler) - n + 1):
            obek = kelimeler[i:i + n]
            a1, a2 = " ".join(obek), "".join(obek)
            uyan = [k for k, (b1, b2) in anahtarli if b1 == a1 or b2 == a2
                    or (len(b1) >= 4 and a1.startswith(b1) and len(a1) - len(b1) <= 4 and " " not in a1[len(b1):])]
            if len(uyan) == 1:
                return uyan[0], kelimeler[:i] + kelimeler[i + n:]
    if bulanik:
        for n in range(min(3, len(kelimeler)), 0, -1):
            for i in range(len(kelimeler) - n + 1):
                a1 = " ".join(kelimeler[i:i + n])
                if len(a1) >= 3:
                    onek = [k for k, (b1, _b2) in anahtarli if b1.startswith(a1)]
                    if len(onek) == 1:
                        return onek[0], kelimeler[:i] + kelimeler[i + n:]
                    if len(onek) > 1:
                        continue      # "yen" → Yeni mi Yenidoğan mı: bulanık seçim yok, eslestir adayları sorar
                puanli = sorted(((_benzerlik(a1, b1), k) for k, (b1, _b2) in anahtarli), key=lambda x: -x[0])
                if puanli and puanli[0][0] >= 0.85 and (len(puanli) == 1 or puanli[0][0] - puanli[1][0] >= 0.06):
                    return puanli[0][1], kelimeler[:i] + kelimeler[i + n:]
    return None, kelimeler


def metin_coz(metin: Any, son: Optional[float] = None, ada: Any = None,
              parsel: Any = None) -> Dict[str, Any]:
    """Serbest metin → {"il","ilce","mahalle","ada","parsel"} ya da {"enlem","boylam"}.
    `ada`/`parsel` ayrıca verilmişse metinde yalnız yer adı bulunması yeter."""
    s = _temiz(metin, 300)
    ap = ada_parsel_bul(s)
    verilen = ada not in (None, "") and parsel not in (None, "")
    if ap is None and not verilen:
        m = _KOORDINAT.search(s)
        if m:
            e, b = (float(x.replace(",", ".")) for x in m.groups())
            if TR_BOYLAM[0] <= e <= TR_BOYLAM[1] and TR_ENLEM[0] <= b <= TR_ENLEM[1] and not (
                    TR_ENLEM[0] <= e <= TR_ENLEM[1]):
                e, b = b, e
            e, b = _koordinat(e, b)
            return {"enlem": e, "boylam": b}
        raise BilgiEksik("Metinde ada/parsel bulunamadı. Örnek: 'İstanbul Kadıköy Caferağa 123 ada 45 parsel' ya da "
                         "koordinat '40.98765, 29.02345'.")
    if ap is not None:
        bas, bit = ap[2]
        kelimeler = _kelimeler(_kucuk(s)[:bas] + " " + _kucuk(s)[bit:])
    else:
        kelimeler = _kelimeler(s)
    if verilen:
        ada, parsel = _parca(ada, "ada", 0, 999_999), _parca(parsel, "parsel", 1, 999_999)
    else:
        ada, parsel = ap[0], ap[1]
    iller = il_listesi(son)
    il_k, kalan = _ara(kelimeler, iller)
    if il_k is None:
        eslesen = []
        for il_id, k in _onbellekteki_ilceler():
            b1 = katla(k["ad"])
            if any(w == b1 or (len(b1) >= 4 and w.startswith(b1) and len(w) - len(b1) <= 4) for w in kelimeler):
                eslesen.append((il_id, k))
        if len({e[0] for e in eslesen}) == 1 and len(eslesen) == 1:
            il_k = next(i for i in iller if i["id"] == eslesen[0][0])
        else:
            raise BilgiEksik("İl adı bulunamadı; il, ilçe ve mahalleyi birlikte yazın: 'İstanbul Kadıköy Caferağa "
                             "%d ada %d parsel'." % (ada, parsel))
    ilce_k, kalan = _ara(kalan, ilce_listesi(il_k["id"], son))
    if ilce_k is None:
        raise BilgiEksik("%s ilinde metindeki ilçe adı bulunamadı; ilçeyi tam yazın." % il_k["ad"])
    mahalleler = mahalle_listesi(ilce_k["id"], son)
    mah_k, kalan = _ara(kalan, mahalleler, bulanik=True)
    if mah_k is None:
        if not kalan:
            raise BilgiEksik("%s / %s: mahalle ya da köy adı eksik." % (il_k["ad"], ilce_k["ad"]))
        try:
            mah_k = eslestir(" ".join(kalan), mahalleler, "mahalle")   # Belirsiz/Bulunamadi önerileriyle
        except Belirsiz as exc:
            exc.ek = {"ada": ada, "parsel": parsel}
            raise
    return {"il": il_k, "ilce": ilce_k, "mahalle": mah_k, "ada": ada, "parsel": parsel}


# --------------------------------------------------------------------------- #
# Onay kartı ve durum                                                          #
# --------------------------------------------------------------------------- #
def onay_karti() -> str:
    return (
        "TKGM canlı sorgu · bu sohbet için tek onay\n\n"
        "ArthurLegal açık kaynaklı, ticari olmayan bir projedir. Parsel bilgisi TKGM Parsel Sorgu'nun "
        "herkese açık verisinden, sizin adınıza getirilir; yer adları OpenStreetMap'te aranır.\n\n"
        "· İstekler sıraya alınır, TKGM'ye tek tek gider (dakikada en çok %d)\n"
        "· Aynı parsel bir gün önbellekten gelir\n"
        "· Bilgi amaçlıdır; malik, şerh, rehin içermez\n\n"
        "Nasıl tasarlandı: %s\n\n"
        "Devam edilsin mi? (evet / hayır)" % (dakikada_toplam(), MANIFESTO))


def onay_gerekli() -> bool:
    return os.environ.get("TKGM_ONAY_KARTI", "1").strip().lower() not in ("0", "false", "hayir", "hayır", "off")


def dakikada_toplam() -> int:
    """Bütün makinelerin toplamı: makine başına 60 / aralık × makine sayısı."""
    return int(60 // TKGM.sinir.taban) * MAKINE


def sinir_metni() -> str:
    makine = " (%d makinenin toplamı; makine başına" % MAKINE if MAKINE > 1 else " ("
    return ("dakikada en çok %d TKGM isteği%s aynı anda tek istek, başlangıçlar arası en az %s sn), sırada en "
            "çok %d sn bekleme, günde en çok %d istek" % (
                dakikada_toplam(), makine, ("%g" % TKGM.sinir.taban).replace(".", ","),
                int(TKGM.sinir.kuyruk_sn), TKGM.gunluk.azami * MAKINE))


def durum(sayilar: bool = False) -> Dict[str, Any]:
    """sayilar=False (paylaşılan uç): başka kullanıcıların etkinliğine yan kanal olacak sayı verilmez."""
    tkgm: Dict[str, Any] = {
        "acik": TKGM.acik(), "sinir": sinir_metni(), "anlik_aralik_sn": round(TKGM.sinir.aralik, 2),
        "devre": TKGM.devre.durum(),
        "dogrulanmamis_uclar": sorted(set(UCLAR) - DOGRULANAN),
        "onay_karti": "açık" if onay_gerekli() else "kapalı (TKGM_ONAY_KARTI=0)"}
    osm: Dict[str, Any] = {"acik": OSM.acik(), "sinir": "saniyede en çok bir istek (Nominatim kullanım koşulu)",
                           "devre": OSM.devre.durum()}
    if sayilar:
        tkgm["bugun_gonderilen"] = TKGM.gunluk.sayi
        tkgm["onbellek_kaydi"] = len(ONBELLEK)
        osm["bugun_gonderilen"] = OSM.gunluk.sayi
    return {"tkgm": tkgm, "nominatim": osm, "kimlik": kimlik(), "manifesto": MANIFESTO}
