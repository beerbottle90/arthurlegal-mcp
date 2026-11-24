"""ortak — kalite bekçilerinin (mevzuat_bekcisi.py, model_bekcisi.py) ortak parçaları.

* Nazik istemci: yalnız izin verilen alan adları, kısa ve açık tanıtım (User-Agent), iki istek arasında en az
  `aralik` saniye, koşu başına en çok `sinir` istek. Yönlendirme izinli alanın dışına çıkamaz.
* Issue planı: değişiklik issue'su aynı başlıkla daha önce açıldıysa (açık ya da kapalı) yenisi açılmaz;
  uyarı issue'su yalnız aynı başlıklı açık issue varsa açılmaz (kapatılan uyarı tekrarlarsa yeniden açılır).
* `gh` yalnız issue ve etiket için kullanılır; bekçiler depoya commit atmaz, sır kullanmaz (Actions'ın kendi
  GITHUB_TOKEN'ı dışında).
"""

from __future__ import annotations

import html as _html
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

UA = "Mozilla/5.0 ArthurLegal-bekci/1.0"
# Bazı siteler (developers.openai.com) text/markdown kabul edilince farklı içerik verir; kaynak kendi değerini seçer.
KABUL = "text/html,*/*;q=0.5"
ZAMAN_ASIMI = 20.0
AZAMI_BAYT = 4_000_000

Getirici = Callable[[str, float], Tuple[int, bytes]]


class ErisimHatasi(Exception):
    """Sayfaya ulaşılamadı, izin dışı adres ya da istek sınırı doldu."""


def izinli(url: str, alanlar: Iterable[str]) -> bool:
    """Yalnız https, izinli alan adı, varsayılan port, kullanıcı adı yok."""
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and (p.hostname or "").lower() in set(alanlar)
                and p.port in (None, 443) and not p.username and not p.password)
    except ValueError:
        return False


def http_getirici(alanlar: Iterable[str], azami: int = AZAMI_BAYT) -> Getirici:
    """(durum, gövde) döndüren getirici. HTTP hata kodu döner; ağ hatası ErisimHatasi olur."""
    alanlar = frozenset(a.lower() for a in alanlar)

    class _Yonlendirme(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
            if not izinli(newurl, alanlar):
                raise ErisimHatasi("İzin dışı yönlendirme reddedildi: %s" % newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    acici = urllib.request.build_opener(_Yonlendirme())

    def getir(url: str, zaman_asimi: float = ZAMAN_ASIMI, kabul: str = KABUL) -> Tuple[int, bytes]:
        if not izinli(url, alanlar):
            raise ErisimHatasi("İzin dışı adres reddedildi: %s" % url)
        istek = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": kabul})
        try:
            with acici.open(istek, timeout=zaman_asimi) as cevap:
                govde = cevap.read(azami + 1)
                if len(govde) > azami:
                    raise ErisimHatasi("Cevap beklenenden büyük: %s" % url)
                return cevap.status, govde
        except urllib.error.HTTPError as exc:
            return exc.code, b""
        except ErisimHatasi:
            raise
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
            neden = getattr(exc, "reason", None) or exc
            raise ErisimHatasi("%s: %s" % (type(exc).__name__, neden)) from None

    return getir


class Nazik:
    """İstekleri seyrekleştirir ve sayar. Testler sahte getirici, uyu ve sayaç verir."""

    def __init__(self, getir: Getirici, aralik: float = 1.5, sinir: int = 60,
                 uyu: Callable[[float], None] = time.sleep, sayac: Callable[[], float] = time.monotonic,
                 sure: Optional[float] = None) -> None:
        self._getir = getir
        self._aralik = aralik
        self.sinir = sinir
        self._uyu = uyu
        self._sayac = sayac
        self._sure = sure            # sn; Actions iş sınırından önce bitsin, uyarı issue'su açılabilsin
        self._ilk: Optional[float] = None
        self._son: Optional[float] = None
        self.sayi = 0

    def neden(self) -> Optional[str]:
        """İstek yapılamıyorsa nedeni (istek ya da süre sınırı), yoksa None."""
        if self.sayi >= self.sinir:
            return "koşu başına istek sınırı (%d) doldu" % self.sinir
        if self._sure is not None and self._ilk is not None and self._sayac() - self._ilk > self._sure:
            return "koşu süre sınırı (%d sn) doldu" % self._sure
        return None

    @property
    def kalan(self) -> int:
        return 0 if self.neden() else self.sinir - self.sayi

    def __call__(self, url: str, zaman_asimi: float = ZAMAN_ASIMI, **kw: Any) -> Tuple[int, bytes]:
        neden = self.neden()
        if neden:
            raise ErisimHatasi(neden[0].upper() + neden[1:])
        if self._ilk is None:
            self._ilk = self._sayac()
        if self._son is not None:
            bekle = self._aralik - (self._sayac() - self._son)
            if bekle > 0:
                self._uyu(bekle)
        self.sayi += 1
        try:
            return self._getir(url, zaman_asimi, **kw)
        finally:
            self._son = self._sayac()


# --------------------------------------------------------------------------- metin
def duz(parca: str) -> str:
    """HTML parçasını düz metne çevirir (etiketler boşluk, varlıklar çözülür, boşluklar teke iner)."""
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", parca or "")).split())


def kisalt(metin: str, n: int) -> str:
    metin = " ".join(str(metin or "").split())
    return metin if len(metin) <= n else metin[: n - 1].rstrip(" ,;:") + "…"


def tr_tarih(iso: str) -> str:
    """'2026-11-17' → '17.11.2026'; '2026-11' → '11.2026'."""
    p = iso.split("-")
    return ".".join(reversed(p))


# --------------------------------------------------------------------------- issue
def planla(adaylar: List[Dict[str, str]], mevcut: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Açılacak issue'lar. aday: {title, body, tur: 'degisiklik'|'uyari'}; mevcut: [{title, state}]."""
    hepsi = {m.get("title") for m in mevcut}
    acik = {m.get("title") for m in mevcut if str(m.get("state", "")).upper() == "OPEN"}
    out: List[Dict[str, str]] = []
    gorulen = set()
    for a in adaylar:
        t = a["title"]
        engel = acik if a.get("tur") == "uyari" else hepsi
        if t in engel or t in gorulen:
            continue
        gorulen.add(t)
        out.append(a)
    return out


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def mevcut_issuelar(etiket: str) -> List[Dict[str, str]]:
    return json.loads(gh("issue", "list", "--state", "all", "--label", etiket, "--limit", "1000",
                         "--json", "title,state") or "[]")


def acik_issue(etiket: str, baslik: str) -> Optional[Dict[str, Any]]:
    """Bu başlıklı açık issue ({number, title, body}) ya da None. Bekçinin koşudan koşuya taşıdığı durum."""
    for i in json.loads(gh("issue", "list", "--state", "open", "--label", etiket, "--limit", "100",
                           "--json", "number,title,body") or "[]"):
        if i.get("title") == baslik:
            return i
    return None


def issue_guncelle(no: int, govde: str) -> None:
    gh("issue", "edit", str(no), "--body", govde)


def issue_kapat(no: int, yorum: str) -> None:
    gh("issue", "close", str(no), "--comment", yorum)


def etiket_kur(etiket: str, renk: str, aciklama: str) -> None:
    gh("label", "create", etiket, "--color", renk, "--force", "--description", aciklama)


def issue_ac(baslik: str, govde: str, etiket: str) -> None:
    gh("issue", "create", "--title", baslik, "--body", govde, "--label", etiket)


def issuelari_ac(adaylar: List[Dict[str, str]], etiket: str, renk: str, aciklama: str,
                 kuru: bool) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Mevcut issue'lara bakar, planlar ve (kuru değilse) açar. Dönüş: (açılan/açılacak, hata)."""
    mevcut: List[Dict[str, str]] = []
    try:
        if not kuru:
            etiket_kur(etiket, renk, aciklama)
        mevcut = mevcut_issuelar(etiket)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        if not kuru:
            return [], "gh kullanılamadı: %s" % exc
    ac = planla(adaylar, mevcut)
    if not kuru:
        for i in ac:
            try:
                issue_ac(i["title"], i["body"], etiket)
            except (OSError, subprocess.CalledProcessError) as exc:
                return ac, "issue açılamadı (%s): %s" % (i["title"], exc)
    return ac, None


# --------------------------------------------------------------------------- çıktı
def ozet_yaz(metin: str) -> None:
    """Koşu özetini hem çıktıya hem Actions özet sayfasına yazar."""
    sys.stdout.write(metin)
    yol = os.environ.get("GITHUB_STEP_SUMMARY")
    if yol:
        with open(yol, "a", encoding="utf-8") as fh:
            fh.write(metin)


def uyari(metin: str) -> None:
    """Actions'ta sarı uyarı satırı (koşu sayfasında görünür)."""
    sys.stdout.write("::warning::%s\n" % " ".join(str(metin).split()))


def json_oku(yol: Optional[str]) -> Dict[str, Any]:
    if not yol:
        return {}
    try:
        with open(yol, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def json_yaz(yol: str, veri: Any) -> None:
    d = os.path.dirname(os.path.abspath(yol))
    os.makedirs(d, exist_ok=True)
    with open(yol, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(veri, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
