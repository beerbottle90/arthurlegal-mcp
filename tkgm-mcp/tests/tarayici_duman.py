"""Arayüz duman testi: "TKGM'den getir" formunu gerçek bir tarayıcıda uçtan uca sürer.

    python tests/tarayici_duman.py [ekran-klasörü]

Başsız Chrome (yoksa Edge) DevTools protokolüyle açılır; TKGM yerine test_canli.py'deki kurgusal
sahte sunucu kullanılır, TKGM'ye ya da OpenStreetMap'e hiçbir istek gitmez. Onay kartı gerçek
confirm() olarak açılır ve kabul edilir. Denetimler: alan sırası ve kilitleri, rakam süzgeci,
"parsel 0" reddi, kademeli listeler, Sorgula, gelen kaydın istenenle karşılaştırılması, Temizle,
Sor sekmesinden kutulara aktarım (belirsiz mahalle), koordinat kutusu, JS istisnası. unittest bunu
toplamaz (ad test_ ile başlamaz): tarayıcı gerektirir. Yalnız stdlib; süreçler PID ile kapatılır."""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

DEPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DEPO)
sys.path.insert(0, os.path.join(DEPO, "tests"))
from test_canli import _Sahte  # noqa: E402

TARAYICILAR = (
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"Microsoft\Edge\Application\msedge.exe"),
    shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def bos_kapi():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WS:
    """DevTools için en küçük WebSocket istemcisi (metin çerçeveleri, maskeli gönderim)."""

    def __init__(self, url):
        u = urllib.parse.urlparse(url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=90)
        anahtar = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (u.path, u.hostname, u.port, anahtar)).encode())
        yanit = b""
        while b"\r\n\r\n" not in yanit:
            yanit += self.s.recv(4096)
        bas, self.artik = yanit.split(b"\r\n\r\n", 1)
        if b" 101 " not in bas.split(b"\r\n")[0]:
            raise RuntimeError(bas.decode("latin-1"))
        self.n = 0
        self.olaylar, self.diyaloglar, self.istisnalar, self.konsol = [], [], [], []

    def _oku(self, n):
        while len(self.artik) < n:
            parca = self.s.recv(1 << 16)
            if not parca:
                raise EOFError("bağlantı kapandı")
            self.artik += parca
        v, self.artik = self.artik[:n], self.artik[n:]
        return v

    def gonder(self, metin):
        b = metin.encode("utf-8")
        maske, n = os.urandom(4), len(b)
        if n < 126:
            bas = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            bas = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            bas = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        self.s.sendall(bas + maske + bytes(c ^ maske[i % 4] for i, c in enumerate(b)))

    def al(self):
        veri = b""
        while True:
            b1, b2 = self._oku(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._oku(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._oku(8))[0]
            if b2 & 0x80:
                self._oku(4)
            parca, op = self._oku(n), b1 & 0x0F
            if op == 0x8:
                raise EOFError("sunucu kapattı")
            if op in (0x9, 0xA):
                continue
            veri += parca
            if b1 & 0x80:
                return json.loads(veri.decode("utf-8"))

    def _isle(self, m):
        yontem = m.get("method")
        if yontem == "Page.javascriptDialogOpening":
            # Onay kartı gerçek confirm() ile açılır; kullanıcı "Tamam"a basmış gibi kabul edilir.
            self.diyaloglar.append(m["params"])
            self.n += 1
            self.gonder(json.dumps({"id": self.n, "method": "Page.handleJavaScriptDialog", "params": {"accept": True}}))
        elif yontem == "Runtime.exceptionThrown":
            self.istisnalar.append(m["params"]["exceptionDetails"])
        elif yontem == "Runtime.consoleAPICalled" and m["params"]["type"] in ("error", "assert"):
            self.konsol.append(m["params"])
        self.olaylar.append(m)

    def komut(self, yontem, **p):
        self.n += 1
        benim = self.n
        self.gonder(json.dumps({"id": benim, "method": yontem, "params": p}))
        while True:
            m = self.al()
            if m.get("id") == benim:
                if "error" in m:
                    raise RuntimeError("%s: %s" % (yontem, m["error"]))
                return m.get("result", {})
            self._isle(m)

    def olay_bekle(self, yontem, sure=30):
        son = time.time() + sure
        while time.time() < son:
            for i, m in enumerate(self.olaylar):
                if m.get("method") == yontem:
                    return self.olaylar.pop(i)
            self._isle(self.al())
        raise RuntimeError("olay gelmedi: " + yontem)


def js(ws, ifade):
    r = ws.komut("Runtime.evaluate", expression=ifade, awaitPromise=True, returnByValue=True, userGesture=True)
    if "exceptionDetails" in r:
        raise RuntimeError("JS: " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:600])
    return r["result"].get("value")


def kosul(ws, ifade, ms=45000):
    return js(ws, "new Promise((ok, no) => { const t0 = Date.now(); (function d() { let v; try { v = (%s); } catch (e) { v = false; }"
                  " if (v) ok(v); else if (Date.now() - t0 > %d) no(new Error('zaman aşımı: ' + %s)); else setTimeout(d, 100); })(); })"
              % (ifade, ms, json.dumps(ifade)))


def bekle_http(url, sure=30):
    son = time.time() + sure
    while time.time() < son:
        try:
            return urllib.request.urlopen(url, timeout=2).read()
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("yanıt yok: " + url)


SONUC = []


def dogrula(ad, tamam, ayrinti=""):
    SONUC.append((ad, bool(tamam)))
    print(("OK    " if tamam else "HATA  ") + ad + ("  | " + str(ayrinti)[:300] if ayrinti != "" else ""), flush=True)


def ekran(ws, ad):
    veri = ws.komut("Page.captureScreenshot", format="png")["data"]
    yol = os.path.join(EKRAN, ad)
    with open(yol, "wb") as f:
        f.write(base64.b64decode(veri))
    return yol


def sec(ws, kutu, deger):
    js(ws, "F(%s).value = %s, F(%s).dispatchEvent(new Event('change', {bubbles: true})), true"
       % (json.dumps(kutu), json.dumps(str(deger)), json.dumps(kutu)))


def yaz(ws, kutu, deger):
    js(ws, "F(%s).value = %s, F(%s).dispatchEvent(new Event('input', {bubbles: true})), true"
       % (json.dumps(kutu), json.dumps(str(deger)), json.dumps(kutu)))


DURUM = ("({uyari: F('uyari').textContent, sorgula: F('sorgula').disabled, il: F('il').value, ilce: F('ilce').value,"
         " ilceKilit: F('ilce').disabled, mahalle: F('mahalle').value, mahalleKilit: F('mahalle').disabled,"
         " ada: F('ada').value, parsel: F('parsel').value, parselKirmizi: F('parsel').classList.contains('gecersiz'),"
         " konum: F('konum').disabled})")
SONUC_METNI = "(() => { const t = $('#canli-sonuc').textContent; return t && !/soruluyor|alınıyor/.test(t) ? t : false; })()"


EKRAN = ""


def main():
    global EKRAN
    tarayici = next((t for t in TARAYICILAR if t and os.path.isfile(t)), None)
    if not tarayici:
        print("Chrome ya da Edge bulunamadı; duman testi atlandı.")
        return 2
    gecici = tempfile.mkdtemp(prefix="tapu-duman-")
    EKRAN = sys.argv[1] if len(sys.argv) > 1 else gecici
    os.makedirs(EKRAN, exist_ok=True)
    profil, cikti = os.path.join(gecici, "profil"), os.path.join(gecici, "cikti")
    os.makedirs(cikti)

    sahte = ThreadingHTTPServer(("127.0.0.1", 0), _Sahte)
    sahte.kilit = threading.Lock()
    sahte.istekler, sahte.mod, sahte.gecikme = [], "", 0.0
    threading.Thread(target=sahte.serve_forever, daemon=True).start()
    kok = "http://127.0.0.1:%d/" % sahte.server_address[1]
    ui_kapi, cdp_kapi = bos_kapi(), bos_kapi()

    ortam = {k: v for k, v in os.environ.items() if not k.startswith("TKGM_")}
    ortam.update(PYTHONIOENCODING="utf-8", TKGM_API_URL=kok + "api/", TKGM_WEB_URL=kok + "web/",
                 TKGM_NOMINATIM_URL=kok + "nominatim/", TKGM_TARAYICI="0", TKGM_CIKTI=cikti)
    ui_log = open(os.path.join(gecici, "ui.log"), "w", encoding="utf-8")
    surecler = []
    try:
        surecler.append(subprocess.Popen([sys.executable, "-B", "server.py", "--ui", "--kapi", str(ui_kapi)], cwd=DEPO,
                                         env=ortam, stdout=ui_log, stderr=subprocess.STDOUT))
        bekle_http("http://127.0.0.1:%d/" % ui_kapi)
        surecler.append(subprocess.Popen([tarayici, "--headless=new", "--remote-debugging-port=%d" % cdp_kapi,
                                          "--user-data-dir=" + profil, "--no-first-run", "--no-default-browser-check",
                                          "--disable-extensions", "--window-size=1440,1000", "about:blank"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        hedefler = json.loads(bekle_http("http://127.0.0.1:%d/json/list" % cdp_kapi))
        ws = WS(next(h for h in hedefler if h["type"] == "page")["webSocketDebuggerUrl"])
        try:
            senaryo(ws, "http://127.0.0.1:%d/" % ui_kapi, sahte)
        finally:
            try:
                ws.komut("Browser.close")
            except Exception:
                pass
    finally:
        for s in reversed(surecler):          # önce tarayıcı (kendi kapanışını bekle), sonra arayüz
            try:
                s.wait(timeout=8 if s is surecler[-1] and len(surecler) == 2 else 0.1)
            except subprocess.TimeoutExpired:
                s.terminate()
                s.wait(timeout=10)
        ui_log.close()
        sahte.shutdown()
        sahte.server_close()
        shutil.rmtree(profil, ignore_errors=True)
        shutil.rmtree(cikti, ignore_errors=True)
        if EKRAN != gecici:
            shutil.rmtree(gecici, ignore_errors=True)
    basarisiz = [a for a, t in SONUC if not t]
    print("\n%d denetim, %d hata%s" % (len(SONUC), len(basarisiz), (": " + "; ".join(basarisiz)) if basarisiz else ""))
    return 1 if basarisiz else 0


def senaryo(ws, adres, sahte):
    ws.komut("Page.enable")
    ws.komut("Runtime.enable")
    ws.komut("Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False)
    ws.komut("Page.navigate", url=adres)
    ws.olay_bekle("Page.loadEventFired")
    kosul(ws, "document.readyState === 'complete' && !!document.querySelector('#f-il')")

    d = js(ws, DURUM)
    sira = js(ws, "[...document.querySelectorAll('#f label')].map(l => l.htmlFor).filter(Boolean)")
    dogrula("1 ilk açılış: tüm zorunlu alanlar eksik", d["uyari"] == "Eksik: il, ilçe, mahalle/köy, ada (köyde 0), parsel", d["uyari"])
    dogrula("1 Sorgula kapalı, ilçe ve mahalle kilitli", d["sorgula"] and d["ilceKilit"] and d["mahalleKilit"] and d["konum"], d)
    dogrula("1 alanlar TKGM sırasıyla alt alta", sira[:5] == ["f-il", "f-ilce", "f-mahalle", "f-ada", "f-parsel"], sira)
    dogrula("1 eski serbest metin kutusu yok", js(ws, "!document.querySelector('#canli-metin')"))
    dogrula("1 onaydan önce TKGM'ye istek yok", not ws.diyaloglar and not sahte.istekler, sahte.istekler)

    js(ws, "F('il').dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true})), true")
    iller = kosul(ws, "F('il').options.length > 1 && [...F('il').options].map(o => o.textContent)")
    kart = ws.diyaloglar[0]["message"] if ws.diyaloglar else ""
    dogrula("2 il kutusuna tıklayınca onay kartı açıldı", len(ws.diyaloglar) == 1 and ws.diyaloglar[0]["type"] == "confirm", kart.splitlines()[:1])
    dogrula("2 kart manifesto dilini taşıyor", "ticari" in kart and "dakikada" in kart.lower(), kart[:200].replace("\n", " / "))
    dogrula("2 il listesi TKGM'den, Türkçe sıralı", iller == ["İl seçin", "Ankara", "İstanbul", "İzmir"], iller)

    sec(ws, "il", 34)
    ilceler = kosul(ws, "!F('ilce').disabled && F('ilce').options.length > 1 && [...F('ilce').options].map(o => o.textContent)")
    d = js(ws, DURUM)
    dogrula("3 il seçilince ilçeler geldi, mahalle kilitli", d["mahalleKilit"] and "Kadıköy" in ilceler, ilceler)
    dogrula("3 uyarı sıradaki eksiği söylüyor", d["uyari"].startswith("Eksik: ilçe,"), d["uyari"])

    sec(ws, "ilce", 1001)
    mahalleler = kosul(ws, "!F('mahalle').disabled && F('mahalle').options.length > 1 && [...F('mahalle').options].map(o => o.textContent)")
    dogrula("4 ilçe seçilince mahalleler geldi", "Deneme" in mahalleler, mahalleler)
    sec(ws, "mahalle", 5001)

    yaz(ws, "ada", "1a0b1")
    yaz(ws, "parsel", "0")
    d = js(ws, DURUM)
    dogrula("5 ada kutusu harfi kabul etmiyor", d["ada"] == "101", d["ada"])
    dogrula("5 parsel 0 reddedildi, kutu kırmızı, Sorgula kapalı", "parsel 0 olamaz" in d["uyari"] and d["parselKirmizi"] and d["sorgula"], d["uyari"])
    yaz(ws, "parsel", "7 ")
    d = js(ws, DURUM)
    dogrula("5 eksiksiz formda özet ve açık Sorgula",
            not d["sorgula"] and d["uyari"] == "Sorgulanacak: İstanbul / Kadıköy / Deneme · 101 ada 7 parsel", d["uyari"])

    js(ws, "F('sorgula').click(), true")
    metin = kosul(ws, SONUC_METNI)
    ozet = js(ws, "({sekme: (document.querySelector('.sek.acik') || {}).id, uyarilar: [...document.querySelectorAll('#canli-sonuc .uyari')].map(x => x.textContent),"
                  " liste: [...document.querySelectorAll('#liste .pk b')].map(x => x.textContent)})")
    dogrula("6 Sorgula: kayıt geldi, uyarı yok", not ozet["uyarilar"] and "101/7" in metin, (metin, ozet["uyarilar"]))
    dogrula("6 parsel listeye eklendi, Özet sekmesi açıldı", ozet["sekme"] == "ozet" and any("101/7" in x for x in ozet["liste"]), ozet)
    dogrula("6 onay kartı sekmede yalnız bir kez", len(ws.diyaloglar) == 1)
    print("      ekran:", ekran(ws, "form_1_sorgu.png"))

    js(ws, "F('temizle').click(), true")
    d = js(ws, DURUM)
    dogrula("7 Temizle formu sıfırladı", d["uyari"].startswith("Eksik: il, ilçe") and d["sorgula"] and d["ilceKilit"] and d["mahalleKilit"] and not d["ada"], d)

    parsel_istekleri = lambda: [y for y, _ in sahte.istekler if y.startswith("/api/parsel/")]  # noqa: E731
    once = len(parsel_istekleri())
    js(ws, "sekmeAc('sor'), true")
    kosul(ws, "!!document.querySelector('#sor textarea')")
    js(ws, "(() => { const g = document.querySelector('#sor textarea'); g.value = 'Kadıköy Yen 101 ada 8 parsel';"
           " [...document.querySelectorAll('#sor button')].find(b => /^Sor/.test(b.textContent)).click(); return true; })()")
    mesaj = kosul(ws, "(() => { const u = [...document.querySelectorAll('#sor .uyari')].map(x => x.textContent).join(' '); return u || false; })()")
    d = js(ws, DURUM)
    dogrula("8 Sor metni kutulara aktarıldı, sorgu atılmadı", d["il"] == "34" and d["ilce"] == "1001" and d["ada"] == "101"
            and d["parsel"] == "8" and len(parsel_istekleri()) == once, d)
    dogrula("8 belirsiz mahalle kullanıcıya seçtiriliyor", d["mahalle"] == "" and d["uyari"] == "Eksik: mahalle/köy" and "birden çok" in mesaj, mesaj)
    dogrula("8 ikinci onay kartı çıkmadı", len(ws.diyaloglar) == 1)
    adaylar = js(ws, "[...F('mahalle').options].filter(o => o.value).map(o => o.value + ':' + o.textContent)")
    sec(ws, "mahalle", 5002)
    d = js(ws, DURUM)
    dogrula("8 mahalle seçilince Sorgula açıldı", not d["sorgula"] and d["uyari"].endswith("· 101 ada 8 parsel"), (d["uyari"], adaylar))
    js(ws, "F('sorgula').click(), true")
    kosul(ws, "(() => { const t = $('#canli-sonuc').textContent; return /101\\/8/.test(t) || /hata|bulunamadı/i.test(t) ? t : false; })()")
    metin = js(ws, "$('#canli-sonuc').textContent")
    dogrula("8 seçilen mahalleyle kayıt geldi", "101/8" in metin, metin)

    js(ws, "document.querySelector('.koordinat').open = true, true")
    yaz(ws, "enlem", "50")
    yaz(ws, "boylam", "29")
    k1 = js(ws, "({kapali: F('konum').disabled, kirmizi: F('enlem').classList.contains('gecersiz')})")
    dogrula("9 Türkiye dışı koordinat reddedildi", k1["kapali"] and k1["kirmizi"], k1)
    yaz(ws, "enlem", "40,9902")
    yaz(ws, "boylam", "29.0344")
    dogrula("9 geçerli koordinatta düğme açıldı", not js(ws, "F('konum').disabled"))
    js(ws, "F('konum').click(), true")
    kosul(ws, "(() => { const t = $('#canli-sonuc').textContent; return t && !/soruluyor|alınıyor/.test(t) && t !== %s ? t : false; })()" % json.dumps(metin))
    metin2 = js(ws, "$('#canli-sonuc').textContent")
    dogrula("9 noktadaki parsel geldi", "·" in metin2 and "hata" not in metin2.lower(), metin2)
    print("      ekran:", ekran(ws, "form_2_son.png"))

    dogrula("10 sayfada JS istisnası yok", not ws.istisnalar, [e.get("exception", {}).get("description", e.get("text")) for e in ws.istisnalar][:3])
    dogrula("10 konsolda hata yok", not ws.konsol, [[a.get("value") or a.get("description") for a in c["args"]] for c in ws.konsol][:3])


if __name__ == "__main__":
    sys.exit(main())
