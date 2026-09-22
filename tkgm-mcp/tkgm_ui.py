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
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import parse_qs, unquote, urlparse

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
_SAYFA_BASLIKLARI = {"X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                     "Content-Security-Policy": "object-src 'none'; base-uri 'none'; form-action 'none'"}


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
                 "nitelik": p["oznitelik"].get("nitelik"), "pafta": p["oznitelik"].get("pafta"),
                 "mevkii": p["oznitelik"].get("mevkii"), "dosyadaki_alan_m2": olcu.get("tapu_alani_m2"),
                 "rejim": [r["uyari"] for r in hukuk.nitelik_rejimleri(p["oznitelik"].get("nitelik", ""))]}
        out.append(kayit)
    return out


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

        def _kapi(self, belirtec: bool = False) -> bool:
            if not _yerel_host(self.headers.get("Host", "")):
                self._json(403, {"ok": False, "hata": "yalnız localhost"})
                return False
            koken = self.headers.get("Origin")
            if koken and not _yerel_host(urlparse(koken).netloc):
                self._json(403, {"ok": False, "hata": "yabancı köken"})
                return False
            if belirtec:
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
                    sayfa = fh.read().replace(b"__TKGM_BELIRTEC__", BELIRTEC.encode())
                self._gonder(200, sayfa, "text/html; charset=utf-8", dict(_SAYFA_BASLIKLARI))
            elif yol == "/logo.png":
                with open(os.path.join(_UI, "logo.png"), "rb") as fh:
                    self._gonder(200, fh.read(), "image/png")
            elif yol == "/api/parseller":
                self._json(200, {"ok": True, "parseller": ozetler()})
            elif yol == "/api/dosyalar":
                self._json(200, {"ok": True, "klasor": kaynak.cikti_klasoru(), "dosyalar": galeri()})
            elif yol == "/api/cevaplar":
                self._json(200, {"ok": True, "cevaplar": kaynak.cevaplar()})
            elif yol.startswith("/dosya/"):
                ad = unquote(yol[len("/dosya/"):])
                tam = os.path.join(kaynak.cikti_klasoru(), ad)
                # Düz ad şartı: ayraç ya da üst dizin içeren hiçbir istek klasörün dışına çıkamaz.
                if ad != os.path.basename(ad) or not ad.lower().endswith(_GALERI_UZANTILARI) \
                        or not os.path.isfile(tam):
                    self._json(404, {"ok": False, "hata": "dosya yok"})
                    return
                tur = mimetypes.guess_type(ad)[0] or "application/octet-stream"
                ek = {"Referrer-Policy": "no-referrer"}   # harita CDN'e ?t= belirtecini sızdırmasın
                if not ad.lower().endswith((".svg", ".html")):
                    ek["Content-Disposition"] = 'attachment; filename="%s"' % ad.replace('"', "")
                else:
                    # Üretilen sayfa/çizim kendi kökenimizde açılırsa API'ye istek atabilirdi.
                    ek["Content-Security-Policy"] = "sandbox allow-scripts"
                with open(tam, "rb") as fh:
                    self._gonder(200, fh.read(), tur, ek)
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
            if urlparse(self.path).path != "/api/cagir":
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
                self._json(200, {"ok": True, "sonuc": sonuc})
            except McpError as exc:
                self._json(200, {"ok": False, "hata": str(exc)})
            except Exception as exc:  # noqa: BLE001 - arayüz çökmemeli, hatayı göstermeli
                self._json(200, {"ok": False, "hata": "%s: %s" % (type(exc).__name__, exc)})

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


def sunucu(tools: List[Tool], port: int = 8765) -> ThreadingHTTPServer:
    return _Sunucu(("127.0.0.1", port), isleyici(tools))


def calistir(tools: List[Tool], port: int = 8765, tarayici: bool = True) -> None:
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
    sys.stderr.write("ArthurLegal · Tapu arayüzü: %s  (çıktılar: %s)\n" % (adres, kaynak.cikti_klasoru()))
    if tarayici:
        threading.Timer(0.6, webbrowser.open, args=(adres,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
