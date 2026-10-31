"""tkgm_ui — tarayıcıda açılan yerel arayüz (`python server.py --ui`).

Yeni bir API değildir: MCP araç listesini tek bir uçtan (`POST /api/cagir`) açar.
Arayüz ile MCP aynı süreçte, aynı parsel belleğiyle çalışır; araç eklenince
arayüzün arka ucu kendiliğinden genişler.

Yalnız 127.0.0.1'e bağlanır ve localhost'un da bir saldırı yüzeyi olduğunu kabul eder:
açık bir sekmedeki yabancı bir site bu porta istek atabilir. Kilitler —
(1) Host başlığı yerel değilse ret (DNS rebinding); (2) süreç başına RASTGELE bir
belirteç: her POST `X-Tkgm: <belirteç>` başlığı, her /api ve /dosya GET'i `?t=<belirteç>`
taşır. Belirteç yalnız index.html'e gömülür; başka kökenden okunamaz (sabit "1" başlığı
tarayıcıya karşı yeterdi, aynı makinedeki başka bir sürece karşı değil); (3) yabancı
Origin ve `Sec-Fetch-Site: cross-site` ret; (4) sayfa çerçevelenemez (X-Frame-Options),
dosya bağlantıları Referer ile belirteç sızdırmaz. Dosya sunumu yalnız çıktı klasöründeki
düz adlarladır. Windows'ta SO_REUSEADDR kapalıdır: açıkken ikinci bir süreç aynı kapıya
bağlanabiliyordu.

index.html sıkı bir CSP ile sunulur (`_CSP`): harita ve 3D, srcdoc'lu iframe'lerde gösterilir
ve o belgeler politikayı miras alır; izin listesi bu yüzden tam olarak sayfaların yüklediği
adreslerdir. Referer politikası `strict-origin`: OpenStreetMap karo sunucusu Referer'sız
isteğe "Access blocked" karosu döndürür; yalnız köken gider, adresteki belirteç gitmez.

Görüntüleme belirteci (`?g=`): Claude'a dönen `arayuzde_ac` bağlantısı içindir ve YALNIZ
GET /dosya/<ad> açar, hiçbir /api ucunu açmaz. Ayar klasöründe kalıcıdır; böylece arayüzü
hangi süreç sunuyorsa (Claude Desktop'un başlattığı `--ui-ile` ya da masaüstü kısayolu)
bağlantı çalışır.

Esri anahtarı (kullanıcının kendi ArcGIS Location Platform anahtarı) çıktı klasörünün
DIŞINDA durur (`ayar_klasoru`), hiçbir üretilen dosyaya ve araç çıktısına yazılmaz. Sayfaya
yalnız tarayıcıya giden yollarda yerleşir: belirteçli /api/cagir yanıtı ve /dosya'nın HTML
sunumu.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from html import escape as html_kacis
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

import tkgm_canli as canli
import tkgm_harc as harc
import tkgm_harita as harita
import tkgm_hukuk as hukuk
import tkgm_kaynak as kaynak
import tkgm_parsel as parseller
from mcpcore import McpError, Tool
from tkgm_analiz import olc

_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
_GALERI_UZANTILARI = (".svg", ".html", ".docx", ".xlsx", ".csv", ".dxf", ".kml", ".geojson")
_AZAMI_GOVDE = 8_000_000
_AZAMI_BOSALTMA = 64_000_000     # sınırı aşan gövde okunup atılır; okunmazsa ret "ağ hatası" görünür
BELIRTEC = secrets.token_urlsafe(24)
VARSAYILAN_KAPI = 8765
# 3B uyarısının arayüzdeki kopyası da tek kaynaktan (tkgm_harita.UYARI_3D) gelir: sayfa sunulurken
# kaçışlanarak yerleşir; metni değiştiren yalnız bir yeri değiştirir.
_UYARI_YER_TUTUCU = b"__TKGM_UYARI_3D__"

# Sayfanın kendisi satır içi betik ve stildir. Harita ve 3D srcdoc'lu iframe'lerde gösterilir
# ve srcdoc belgesi bu politikayı MİRAS ALIR; onların yüklediği her şey burada izinli olmalıdır.
# Kaynaklar tkgm_harita.py'den çıkarıldı, varsayılmadı:
#   _LEAFLET   https://cdnjs.cloudflare.com/…    → leaflet.min.css + leaflet.min.js (SRI'li)
#   _OSM_KARO  https://tile.openstreetmap.org/…  → karolar <img> olarak (img-src)
#   _THREE     https://cdn.jsdelivr.net/npm/…    → importmap (satır içi) + ES modülleri (script-src)
#   ESRI_KARO  https://ibasemaps-api.arcgis.com/… → uydu karoları; haritada <img>, 3D'de crossOrigin
#              Image → WebGL dokusu. İkisi de img-src'dir; fetch yok, connect-src genişlemez.
# DEM'i ve bina tabanlarını tarayıcı değil SUNUCU çeker ve sayfaya gömer; connect-src'de yalnız
# kendimiz varız. Kroki önizlemesi blob: adresli <img>'dir.
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
    "img-src 'self' data: blob: https://tile.openstreetmap.org https://*.tile.openstreetmap.org "
    "https://cdnjs.cloudflare.com https://ibasemaps-api.arcgis.com",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-src 'self' blob: data:",
    "worker-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
])
_SAYFA_BASLIKLARI = {"X-Frame-Options": "DENY", "Referrer-Policy": harita.KARO_REFERRER,
                     "Content-Security-Policy": _CSP}


def csp() -> str:
    """index.html'in Content-Security-Policy başlığı (testler ve belgeler için)."""
    return _CSP


# --------------------------------------------------------------------------- #
# Ayar klasörü: Esri anahtarı, görüntüleme belirteci                           #
# --------------------------------------------------------------------------- #
_ESRI_DOSYASI = "esri_anahtari.txt"
_GORUNTU_DOSYASI = "goruntu_belirteci.txt"
# Yalnız anahtarın geçerliliğini sınamak için; karo değil, birkaç KB'lık stil tanımı döner.
# Görüntü ucu geçersiz bir anahtara da karo verebiliyor (2026-09 yoklaması): sınama oradan yapılamaz.
_ESRI_SINAMA = "https://basemapstyles-api.arcgis.com/arcgis/rest/services/styles/v2/styles/arcgis/imagery"
_BELIRTEC_BICIMI = re.compile(r"[A-Za-z0-9_-]{24,64}")


def ayar_klasoru() -> str:
    """Çıktı klasöründen AYRI: /dosya ve galeri buraya erişemez."""
    return os.path.normpath(os.environ.get("TKGM_AYAR")
                            or os.path.join(os.path.expanduser("~"), ".arthurlegal", "tkgm"))


def _gizli_yaz(ad: str, icerik: str) -> None:
    os.makedirs(ayar_klasoru(), exist_ok=True)
    yol = os.path.join(ayar_klasoru(), ad)
    gecici = yol + ".tmp"
    with open(gecici, "w", encoding="utf-8") as fh:
        fh.write(icerik)
    try:
        os.chmod(gecici, 0o600)      # POSIX'te yalnız sahibi okur; Windows'ta kullanıcı profili zaten öyle
    except OSError:
        pass
    os.replace(gecici, yol)


def esri_anahtari() -> Optional[str]:
    """Önce TKGM_ESRI_ANAHTARI, sonra ayar dosyası. Biçimi geçersizse yok sayılır."""
    anahtar = (os.environ.get("TKGM_ESRI_ANAHTARI") or "").strip()
    if not anahtar:
        try:
            with open(os.path.join(ayar_klasoru(), _ESRI_DOSYASI), encoding="utf-8") as fh:
                anahtar = fh.read().strip()
        except OSError:
            return None
    return anahtar if harita.esri_anahtari_gecerli(anahtar) else None


def esri_anahtari_kaydet(anahtar: Optional[str]) -> None:
    """Boş/None: dosyayı siler. Geçersiz biçim: ValueError (dosyaya hiçbir şey yazılmaz)."""
    if not anahtar:
        try:
            os.remove(os.path.join(ayar_klasoru(), _ESRI_DOSYASI))
        except FileNotFoundError:
            pass
        return
    if not harita.esri_anahtari_gecerli(anahtar):
        raise ValueError("Anahtar biçimi tanınmadı (20-600 karakter; harf, rakam, . _ ~ -).")
    _gizli_yaz(_ESRI_DOSYASI, anahtar)


def esri_sina(anahtar: str) -> Optional[bool]:
    """True geçerli, False Esri reddetti, None ağ yok (karar verilemedi)."""
    istek = urllib.request.Request(_ESRI_SINAMA + "?token=" + quote(anahtar, safe=""),
                                   headers={"User-Agent": "arthurlegal-tkgm/ui"})
    try:
        with urllib.request.urlopen(istek, timeout=10) as yanit:
            govde = yanit.read(4096)
    except urllib.error.HTTPError as exc:
        return False if exc.code in (400, 401, 403, 498, 499) else None
    except (urllib.error.URLError, OSError):
        return None
    # Esri bazı hataları 200 gövdesinde {"error": {...}} olarak döndürür.
    return not govde.lstrip().startswith(b'{"error"')


def _anahtar_yerlestir(sonuc: Any) -> Any:
    """Harita/3D'nin satır içi HTML'ine anahtarı koyar; yalnız tarayıcıya giden yolda çağrılır."""
    if isinstance(sonuc, dict) and isinstance(sonuc.get("html"), str):
        anahtar = esri_anahtari()
        if anahtar:
            sonuc = dict(sonuc, html=harita.esri_anahtari_ekle(sonuc["html"], anahtar))
    return sonuc


_GORUNTU: Dict[str, Optional[str]] = {"belirtec": None}
_GORUNTU_KILIT = threading.Lock()


def goruntu_belirteci() -> str:
    """`arayuzde_ac` bağlantılarının belirteci: ayar klasöründe kalıcı, süreçler arasında ortak.
    Okunamaz ya da yazılamazsa süreç içi bir belirteç kullanılır (bağlantı yalnız bu süreçte geçer)."""
    with _GORUNTU_KILIT:
        if _GORUNTU["belirtec"]:
            return _GORUNTU["belirtec"]
        yol = os.path.join(ayar_klasoru(), _GORUNTU_DOSYASI)
        try:
            with open(yol, encoding="utf-8") as fh:
                deger = fh.read().strip()
        except OSError:
            deger = ""
        if not _BELIRTEC_BICIMI.fullmatch(deger):
            deger = secrets.token_urlsafe(24)
            try:
                _gizli_yaz(_GORUNTU_DOSYASI, deger)
            except OSError:
                pass
        _GORUNTU["belirtec"] = deger
        return deger


# Bu süreçte arayüz açıksa adresi. `--ui-ile` ile başlayan MCP süreci, kapı başka bir süreçte
# (masaüstü kısayolunun açtığı arayüz) doluysa o arayüzü yoklar; düz stdio ve testler yoklamaz.
_ADRES: Dict[str, Any] = {"kendi": None, "yokla": False, "yoklama": None, "zaman": 0.0}


def yoklamayi_ac() -> None:
    _ADRES["yokla"] = True


def _arayuz_adresi() -> Optional[str]:
    if _ADRES["kendi"]:
        return _ADRES["kendi"]
    if kaynak.uzak_mi() or not _ADRES["yokla"]:
        return None
    simdi = time.monotonic()
    if simdi - _ADRES["zaman"] < 60:
        return _ADRES["yoklama"]
    adres = "http://127.0.0.1:%d/" % VARSAYILAN_KAPI
    bulunan = None
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(adres + "saglik", timeout=0.5) as y:
            if json.loads(y.read(512).decode("utf-8")).get("uygulama") == "tkgm-ui":
                bulunan = adres
    except (OSError, ValueError):
        pass
    _ADRES.update(yoklama=bulunan, zaman=simdi)
    return bulunan


def arayuz_baglantisi(yol: str) -> Optional[str]:
    """Çıktı klasöründeki bir harita/3D/kroki dosyasını Tapu arayüzünde açan yerel bağlantı.

    Arayüz bu süreçte ya da varsayılan kapıda açık değilse None. Bağlantı yalnız o dosyayı
    gösterir (görüntüleme belirteci /api açmaz)."""
    if not yol or kaynak.uzak_mi():
        return None
    ad = os.path.basename(yol)
    if os.path.normcase(os.path.dirname(os.path.abspath(yol))) != os.path.normcase(
            os.path.abspath(kaynak.cikti_klasoru())) or not ad.lower().endswith((".html", ".svg")):
        return None
    adres = _arayuz_adresi()
    if not adres:
        return None
    return "%sdosya/%s?g=%s" % (adres, quote(ad), goruntu_belirteci())


def durum() -> Dict[str, Any]:
    """server_status için: anahtar ve belirteç dönmez, yalnız var olup olmadıkları."""
    return {"acik": bool(_ADRES["kendi"]), "adres": _ADRES["kendi"], "esri_anahtari": esri_anahtari() is not None}


def _yerel_host(host: str) -> bool:
    ad = (host or "").rsplit(":", 1)[0].strip("[]").lower()
    return ad in ("127.0.0.1", "localhost", "::1")


def galeri() -> List[Dict[str, Any]]:
    klasor = kaynak.cikti_klasoru()
    if not os.path.isdir(klasor):
        return []
    out = []
    for ad in os.listdir(klasor):
        yol = os.path.join(klasor, ad)
        if os.path.isfile(yol) and ad.lower().endswith(_GALERI_UZANTILARI):
            out.append({"ad": ad, "kb": round(os.path.getsize(yol) / 1024.0, 1),
                        "degisim": int(os.path.getmtime(yol))})
    return sorted(out, key=lambda x: -x["degisim"])[:200]


def ozetler() -> List[Dict[str, Any]]:
    out = []
    for p in parseller.bellektekiler()[:100]:
        olcu = olc(p)
        kayit = {"ref": p["ref"], "parsel": parseller.etiket(p), "alan_m2": olcu["alan_m2"],
                 "il": p["oznitelik"].get("il"), "ilce": p["oznitelik"].get("ilce"),
                 "nitelik": p["oznitelik"].get("nitelik"), "pafta": p["oznitelik"].get("pafta"),
                 "mevkii": p["oznitelik"].get("mevkii"), "dosyadaki_alan_m2": olcu.get("tapu_alani_m2"),
                 "rejim": [r["uyari"] for r in hukuk.nitelik_rejimleri(p["oznitelik"].get("nitelik", ""))]}
        out.append(kayit)
    return out


_KIMLIK = re.compile(r"\d{1,9}")


def idari(sorgu: Dict[str, List[str]]) -> Any:
    """Sorgu formunun seçim kutuları: il, `il=` ile ilçe, `ilce=` ile mahalle/köy listesi.

    Listeler kullanıcı yazmaz; TKGM'nin kendi kayıtlarından gelir (hız sınırlı kapı, 30 gün
    önbellek). Canlı istek olduğu için onay kartı `onay=1` gelene kadar döner."""
    if canli.onay_gerekli() and (sorgu.get("onay") or [""])[0] != "1":
        return 200, {"ok": True, "onay_gerekli": True, "kart": canli.onay_karti()}
    il, ilce = (sorgu.get("il") or [""])[0], (sorgu.get("ilce") or [""])[0]
    for deger in (il, ilce):
        if deger and not _KIMLIK.fullmatch(deger):
            return 400, {"ok": False, "hata": "kimlik sayı olmalı"}
    son = time.monotonic() + canli.CAGRI_BUTCESI_SN
    try:
        if ilce:
            tur, liste = "mahalle", canli.mahalle_listesi(int(ilce), son)
        elif il:
            tur, liste = "ilce", canli.ilce_listesi(int(il), son)
        else:
            tur, liste = "il", canli.il_listesi(son)
    except canli.CanliHata as exc:
        return 200, {"ok": False, "hata": str(exc)}
    return 200, {"ok": True, "tur": tur, "liste": sorted(liste, key=lambda k: canli.katla_tam(k["ad"]))}


def idari_coz(sorgu: Dict[str, List[str]]) -> Any:
    """Sor sekmesindeki serbest metin → kutucuklara aktarılacak kimlikler. Sorgu YAPMAZ: kullanıcı
    kutucukları görüp Sorgula'ya kendisi basar."""
    if canli.onay_gerekli() and (sorgu.get("onay") or [""])[0] != "1":
        return 200, {"ok": True, "onay_gerekli": True, "kart": canli.onay_karti()}
    metin = (sorgu.get("metin") or [""])[0][:300]
    try:
        c = canli.metin_coz(metin, time.monotonic() + canli.CAGRI_BUTCESI_SN)
    except canli.Belirsiz as exc:
        return 200, {"ok": True, "belirsiz": exc.yanit()}
    except canli.CanliHata as exc:
        return 200, {"ok": False, "hata": str(exc)}
    return 200, dict({"ok": True}, **c)


def isleyici(tools: List[Tool]):
    by_ad = {t.name: t for t in tools}

    class Isleyici(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "tkgm-ui"

        def log_message(self, fmt, *args):  # noqa: A003
            if os.environ.get("MCP_DEBUG"):
                sys.stderr.write("ui %s\n" % (fmt % args))

        def _gonder(self, kod: int, govde: bytes, tur: str = "application/json; charset=utf-8",
                    ek: Dict[str, str] = None) -> None:
            self.send_response(kod)
            self.send_header("Content-Type", tur)
            self.send_header("Content-Length", str(len(govde)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            if kod >= 400:
                # Reddedilen POST'un gövdesi okunmadan atılıyor. HTTP/1.1 kalıcı
                # bağlantısında okunmamış gövde, sonraki isteğin başı sanılır ve
                # bağlantı bozulur (test bunu ConnectionAbortedError olarak yakaladı).
                self.send_header("Connection", "close")
                self.close_connection = True
            for k, v in (ek or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(govde)

        def _json(self, kod: int, veri: Any) -> None:
            self._gonder(kod, json.dumps(veri, ensure_ascii=False, default=str).encode("utf-8"))

        def _goruntu_gecerli(self) -> bool:
            """`?g=` yalnız GET /dosya için ve yalnız üst düzey açılışta (sohbetteki bağlantıya
            tıklama) ya da sekme içi istekte geçer; başka bir sitenin sayfasına gömülemez."""
            yol = urlparse(self.path).path
            if self.command != "GET" or not yol.startswith("/dosya/"):
                return False
            verilen = (parse_qs(urlparse(self.path).query).get("g") or [""])[0]
            if not verilen or not hmac.compare_digest(verilen.encode(), goruntu_belirteci().encode()):
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return (self.headers.get("Sec-Fetch-Mode") == "navigate"
                        and self.headers.get("Sec-Fetch-Dest") == "document")
            return True

        def _kapi(self, belirtec: bool = False) -> bool:
            if not _yerel_host(self.headers.get("Host", "")):
                self._json(403, {"ok": False, "hata": "yalnız localhost"})
                return False
            koken = self.headers.get("Origin")
            if koken and not _yerel_host(urlparse(koken).netloc):
                self._json(403, {"ok": False, "hata": "yabancı köken"})
                return False
            if belirtec:
                if self._goruntu_gecerli():
                    return True
                if self.headers.get("Sec-Fetch-Site") == "cross-site":
                    self._json(403, {"ok": False, "hata": "başka siteden istek"})
                    return False
                verilen = self.headers.get("X-Tkgm") or \
                    (parse_qs(urlparse(self.path).query).get("t") or [""])[0]
                if not hmac.compare_digest(verilen.encode(), BELIRTEC.encode()):
                    self._json(403, {"ok": False, "hata": "belirteç yok ya da geçersiz — sayfayı yenileyin"})
                    return False
            return True

        def do_GET(self):  # noqa: N802
            yol = urlparse(self.path).path
            if not self._kapi(belirtec=yol.startswith(("/api/", "/dosya/"))):
                return
            if yol in ("/", "/index.html"):
                with open(os.path.join(_UI, "index.html"), "rb") as fh:
                    sayfa = (fh.read().replace(b"__TKGM_BELIRTEC__", BELIRTEC.encode())
                             .replace(_UYARI_YER_TUTUCU, html_kacis(harita.UYARI_3D, quote=True).encode("utf-8")))
                self._gonder(200, sayfa, "text/html; charset=utf-8", dict(_SAYFA_BASLIKLARI))
            elif yol in ("/logo.png", "/favicon.ico"):
                with open(os.path.join(_UI, "logo.png"), "rb") as fh:
                    self._gonder(200, fh.read(), "image/png")
            elif yol == "/saglik":
                # Belirteçsiz: başka bir süreç (MCP) arayüzün açık olduğunu buradan anlar. Veri dönmez.
                self._json(200, {"ok": True, "uygulama": "tkgm-ui"})
            elif yol == "/api/ayar":
                # Anahtarın kendisi hiçbir yanıtta dönmez; yalnız var olup olmadığı ve nereden geldiği.
                ortam = bool((os.environ.get("TKGM_ESRI_ANAHTARI") or "").strip())
                self._json(200, {"ok": True, "esri": esri_anahtari() is not None,
                                 "esri_kaynak": "ortam" if ortam else "dosya", "ayar_klasoru": ayar_klasoru()})
            elif yol == "/api/parseller":
                self._json(200, {"ok": True, "parseller": ozetler()})
            elif yol == "/api/dosyalar":
                self._json(200, {"ok": True, "klasor": kaynak.cikti_klasoru(), "dosyalar": galeri()})
            elif yol == "/api/cevaplar":
                self._json(200, {"ok": True, "cevaplar": kaynak.cevaplar()})
            elif yol == "/api/harc_islemleri":
                self._json(200, dict(harc.islemler(), ok=True))
            elif yol in ("/api/idari", "/api/idari/coz"):
                kod, veri = (idari if yol == "/api/idari" else idari_coz)(parse_qs(urlparse(self.path).query))
                self._json(kod, veri)
            elif yol.startswith("/dosya/"):
                ad = unquote(yol[len("/dosya/"):])
                tam = os.path.join(kaynak.cikti_klasoru(), ad)
                # Düz ad şartı: ayraç ya da üst dizin içeren hiçbir istek klasörün dışına çıkamaz.
                if ad != os.path.basename(ad) or not ad.lower().endswith(_GALERI_UZANTILARI) \
                        or not os.path.isfile(tam):
                    self._json(404, {"ok": False, "hata": "dosya yok"})
                    return
                tur = mimetypes.guess_type(ad)[0] or "application/octet-stream"
                # Yalnız köken gider: OSM karoyu Referer'sız vermez, belirteç (?t=, ?g=) adreste kalır.
                ek = {"Referrer-Policy": harita.KARO_REFERRER, "X-Frame-Options": "SAMEORIGIN"}
                if not ad.lower().endswith((".svg", ".html")):
                    ek["Content-Disposition"] = 'attachment; filename="%s"' % ad.replace('"', "")
                else:
                    # Üretilen sayfa/çizim kendi kökenimizde açılırsa API'ye istek atabilirdi.
                    ek["Content-Security-Policy"] = "sandbox allow-scripts"
                with open(tam, "rb") as fh:
                    govde = fh.read()
                if ad.lower().endswith(".html"):
                    anahtar = esri_anahtari()
                    if anahtar:
                        # Dosyada işaret durur; anahtar yalnız bu yanıtta, tarayıcıya giderken yerleşir.
                        govde = harita.esri_anahtari_ekle(govde.decode("utf-8", "replace"), anahtar).encode("utf-8")
                self._gonder(200, govde, tur, ek)
            else:
                self._json(404, {"ok": False, "hata": "yol yok"})

        def do_POST(self):  # noqa: N802
            # Gövde, HER ŞEYDEN ÖNCE okunur — reddedilecek olsa bile. İstemci gövdeyi
            # yollarken soketi kapatmak karşı tarafta bağlantı sıfırlamasıdır: reddin
            # kendisi görünmez olur, tarayıcı "ağ hatası" der. (Test bunu aralıklı bir
            # ConnectionAbortedError olarak yakaladı; sebebi buydu.)
            try:
                boy = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                boy = -1
            ham = self.rfile.read(boy) if 0 < boy <= _AZAMI_GOVDE else b""
            if _AZAMI_GOVDE < boy <= _AZAMI_BOSALTMA:
                kalan = boy
                while kalan > 0:
                    parca = self.rfile.read(min(kalan, 1 << 20))
                    if not parca:
                        break
                    kalan -= len(parca)
                self._json(413, {"ok": False, "hata": "dosya 8 MB sınırını aşıyor"})
                return
            if not self._kapi(belirtec=True):
                return
            yol = urlparse(self.path).path
            if yol == "/api/ayar":
                self._ayar(ham)
                return
            if yol != "/api/cagir":
                self._json(404, {"ok": False, "hata": "yol yok"})
                return
            try:
                if not ham:
                    raise ValueError("gövde yok ya da sınırı aşıyor")
                istek = json.loads(ham.decode("utf-8"))
                arac = by_ad[istek["arac"]]
            except (ValueError, KeyError, UnicodeDecodeError):
                self._json(400, {"ok": False, "hata": "geçersiz istek ya da bilinmeyen araç"})
                return
            try:
                sonuc = arac.handler(istek.get("args") or {})
                if isinstance(sonuc, str):
                    try:
                        sonuc = json.loads(sonuc)
                    except ValueError:
                        pass
                self._json(200, {"ok": True, "sonuc": _anahtar_yerlestir(sonuc)})
            except McpError as exc:
                self._json(200, {"ok": False, "hata": str(exc)})
            except Exception as exc:  # noqa: BLE001 - arayüz çökmemeli, hatayı göstermeli
                self._json(200, {"ok": False, "hata": "%s: %s" % (type(exc).__name__, exc)})

        def _ayar(self, ham: bytes) -> None:
            try:
                istek = json.loads(ham.decode("utf-8")) if ham else {}
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"ok": False, "hata": "geçersiz istek"})
                return
            anahtar = istek.get("esri_anahtari") if isinstance(istek, dict) else None
            if not (anahtar is None or isinstance(anahtar, str)):
                self._json(400, {"ok": False, "hata": "esri_anahtari metin olmalı"})
                return
            if (os.environ.get("TKGM_ESRI_ANAHTARI") or "").strip():
                self._json(409, {"ok": False, "hata": "Anahtar TKGM_ESRI_ANAHTARI ortam değişkeninden geliyor; "
                                                      "değiştirmek için o değişkeni düzenleyin."})
                return
            anahtar = (anahtar or "").strip()
            sinama = None
            if anahtar:
                if not harita.esri_anahtari_gecerli(anahtar):
                    self._json(400, {"ok": False, "hata": "Anahtar biçimi tanınmadı (20-600 karakter; harf, "
                                                          "rakam, . _ ~ -)."})
                    return
                sinama = esri_sina(anahtar)
                if sinama is False:
                    self._json(400, {"ok": False, "hata": "Esri bu anahtarı reddetti. Anahtarın 'Basemaps' "
                                                          "yetkisi açık mı, süresi dolmuş mu?"})
                    return
            try:
                esri_anahtari_kaydet(anahtar or None)
            except (OSError, ValueError) as exc:
                self._json(500, {"ok": False, "hata": "Anahtar kaydedilemedi: %s" % type(exc).__name__})
                return
            self._json(200, {"ok": True, "esri": bool(anahtar), "sinanamadi": bool(anahtar) and sinama is None})

    return Isleyici


class _Sunucu(ThreadingHTTPServer):
    daemon_threads = True
    # Windows'ta SO_REUSEADDR, dolu kapıya ikinci bir süreci de bağlar ("kapı dolu" hiç
    # görünmez, istekler rastgele sürece düşer). Orada münhasır bağlanılır.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def sunucu(tools: List[Tool], port: int = VARSAYILAN_KAPI) -> ThreadingHTTPServer:
    return _Sunucu(("127.0.0.1", port), isleyici(tools))


def calistir(tools: List[Tool], port: int = VARSAYILAN_KAPI, tarayici: bool = True) -> None:
    # TKGM_TARAYICI=0: sekme açma. Kısayolun duman testi bunu kullanır; sunucuyu
    # elle bir terminalde tutan kullanıcının da işine yarar.
    if os.environ.get("TKGM_TARAYICI", "").strip() == "0":
        tarayici = False
    try:
        httpd = sunucu(tools, port)
    except OSError as exc:
        # Çift tıklayan kullanıcı traceback okumaz. En sık sebep zaten açık olan
        # ikinci bir kopyadır ve doğru çözüm yeni sunucu değil, açık olan sekmedir.
        sys.stderr.write(
            "\nArayüz başlatılamadı (%s).\n"
            "En olası sebep: %d numaralı kapı dolu — arayüz zaten açık olabilir.\n"
            "Önce http://127.0.0.1:%d/ adresini deneyin.\n"
            "Başka kapı gerekiyorsa: python server.py --ui --kapi 8766\n"
            % (exc, port, port))
        return
    adres = "http://127.0.0.1:%d/" % httpd.server_address[1]
    _ADRES["kendi"] = adres
    sys.stderr.write("ArthurLegal · Tapu arayüzü: %s  (çıktılar: %s)\n" % (adres, kaynak.cikti_klasoru()))
    if tarayici:
        threading.Timer(0.6, webbrowser.open, args=(adres,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _ADRES["kendi"] = None
        httpd.server_close()
