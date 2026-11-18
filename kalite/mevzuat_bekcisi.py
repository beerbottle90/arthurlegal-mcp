#!/usr/bin/env python3
"""mevzuat_bekcisi — kalite süreci 7: mevzuat ve içtihat değişikliğinin paket rehberlerine etkisi.

Haftada bir (.github/workflows/mevzuat-bekcisi.yml) Resmî Gazete'nin son 7 günlük fihristini (mükerrer sayılar
dahil) okur ve şu yayımları seçer:

* Kanun (Yasama Bölümü; fihristte "––" ile değil numarayla başlar: "7554 Bazı Kanunlarda …")
* Cumhurbaşkanlığı kararnamesi, Cumhurbaşkanı kararı, yönetmelik
* İçtihadı birleştirme kararı (Yargıtay, Danıştay; başlıktan tanınır)

Her yayımın metnini açar (HTML ya da PDF), dokunduğu mevzuatı çıkarır ("6100 sayılı Hukuk Muhakemeleri Kanununun",
"3 sayılı … Cumhurbaşkanlığı Kararnamesi", değişen yönetmeliğin adı) ve ArthurLegal paketlerinde
(`ArthurLegal-*-Public-Release/knowledge/`) o mevzuata numarası, kısaltması (HMK) ya da adıyla atıf yapan
rehberleri listeler. Her değişiklik için bir issue açar; başlık "Mevzuat değişikliği: …" ile başlar (aylık durum
e-postası başlıktan okur). Aynı başlıklı issue (açık ya da kapalı) varsa yenisi açılmaz.

* Kanun, kararname ve içtihadı birleştirme kararı her zaman issue açar (rehber eşleşmese de: yeni konu olabilir).
  İstisna: uluslararası anlaşmanın onaylanmasını uygun bulan kanunlar yalnız rehber eşleşirse.
* Yönetmelik ve Cumhurbaşkanı kararı yalnız bir rehber eşleşirse issue açar. Yere özgü kararlar (acele
  kamulaştırma, özelleştirme, sit alanı, "… İli, … İlçesinde"), üniversite yönetmelikleri `GURULTU` ile elenir
  ve yalnız koşu özetinde sayılır.
* Resmî Gazete'ye ulaşılamaz ya da paketler okunamazsa sessiz geçmez: "Mevzuat bekçisi uyarısı: …" issue'su
  (aynı başlıklı açık issue yoksa) ve Actions uyarısı.

RG okuma kalıbı hesap-mcp/tazelik.py'dendir (yalnız https://www.resmigazete.gov.tr, kısa tanıtım). İstekler
nazik: iki istek arasında en az 1,5 sn, koşu başına en çok 80 istek ve 9 dakika (iş akışının 15 dakikalık sınırından
önce biter; süre dolarsa kalan günler uyarı issue'suna, kalan metinler "okunmadı"ya düşer). Commit atmaz; yalnız
`gh issue`.

    python kalite/mevzuat_bekcisi.py --paketler al-paketler [--gun 7] [--cikti sonuc.json] [--kuru]

`--kuru`: issue açmaz, yalnız ne açılacağını yazar (dallarda ve PR'larda).
"""

from __future__ import annotations

import argparse
import glob
import html as _html
import importlib.util
import io
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple
from urllib.parse import urljoin

BURADA = os.path.dirname(os.path.abspath(__file__))
KOK = os.path.dirname(BURADA)
if BURADA not in sys.path:
    sys.path.insert(0, BURADA)

import ortak  # noqa: E402


def _tazelik_yukle():
    """hesap-mcp/tazelik.py'yi kendi adıyla yükler (hesap-mcp'yi sys.path'e koymadan: server.py çakışmasın)."""
    yol = os.path.join(KOK, "hesap-mcp", "tazelik.py")
    spec = importlib.util.spec_from_file_location("hesap_tazelik", yol)
    modul = importlib.util.module_from_spec(spec)
    sys.modules["hesap_tazelik"] = modul
    spec.loader.exec_module(modul)
    return modul


tz = _tazelik_yukle()
katla = tz.katla

ETIKET = "mevzuat-bekcisi"
RENK = "1D76DB"
ETIKET_ACIKLAMA = "Kalite süreci 7: Resmî Gazete'de mevzuat ve içtihat değişikliği"
UYARI_TARAMA = "Mevzuat bekçisi uyarısı: Resmî Gazete taraması tamamlanamadı"
UYARI_PAKET = "Mevzuat bekçisi uyarısı: paket rehberleri okunamadı"

GUN_VARSAYILAN = 7
GUN_AZAMI = 14
ISTEK_ARALIGI = 1.5          # sn
ISTEK_SINIRI = 80            # koşu başına
SURE_SINIRI = 540.0          # sn; iş akışının 15 dk sınırından önce biter, uyarı issue'su açılabilir
FIHRIST_ZAMAN_ASIMI = 15.0   # sn
METIN_ZAMAN_ASIMI = 30.0     # sn; kararname ve karar PDF'leri
PDF_SAYFA = 40
TABLO_SATIRI = 10           # issue'da mevzuat başına rehber satırı
GOVDE_AZAMI = 60_000        # GitHub issue gövdesi en çok 65 536 karakter

TABLO_YOLU = os.path.join(BURADA, "kanunlar.json")

# kod: (ad, fihrist bölümü (katlanmış), rehber eşleşmese de issue, metin okuma sırası)
TURLER: Dict[str, Dict[str, Any]] = {
    "ibk": {"ad": "İçtihadı birleştirme kararı", "bolum": None, "her_zaman": True, "sira": 0},
    "kanun": {"ad": "Kanun", "bolum": re.compile(r"^kanun(lar)?$"), "her_zaman": True, "sira": 1},
    "cbk": {"ad": "Cumhurbaşkanlığı Kararnamesi", "bolum": re.compile(r"^cumhurbaskanligi kararname(si|leri)$"),
            "her_zaman": True, "sira": 2},
    "yonetmelik": {"ad": "Yönetmelik", "bolum": re.compile(r"^yonetmelik(ler)?$"), "her_zaman": False, "sira": 3},
    "cb_karari": {"ad": "Cumhurbaşkanı Kararı", "bolum": re.compile(r"^cumhurbaskani karar(i|lari)$"),
                  "her_zaman": False, "sira": 4},
}
IBK = re.compile(r"ictiha[dt]\w* birlestirme")
# Uluslararası anlaşmanın onayını uygun bulan kanunlar rehberlere nadiren dokunur: yalnız rehber eşleşirse issue.
UYGUN_BULMA = re.compile(r"onaylanmasinin uygun bulundugu")
# Yere ya da kuruma özgü, rehberleri etkilemeyen yayımlar (katlanmış başlıkta). Elenenler özette sayılır.
GURULTU = re.compile(
    r"kamulastir|ozellestirme idaresi|arazi toplulastirma|universitesi|"
    r"\b(ili|ilinde|illerinde|ilcesi|ilcesinde|ilcelerinde)\b|teknoloji gelistirme bolgesi|organize sanayi bolgesi|"
    r"endustri bolgesi|koruma ve gelisim bolgesi|sit alani|hassas alan|tabiat parki|milli park")

_TIRE = ("––", "--", "–", "—")
_KANUN_KALEMI = re.compile(r"^(\d{3,5})\s+(\S.*)$")
_HARF = "A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû0-9"


# --------------------------------------------------------------------------- fihrist
def fihrist(metin: str, url: str, gun: date) -> Dict[str, Any]:
    """tazelik.fihrist_ayristir + Yasama Bölümü'ndeki kanun kalemleri (numarayla başlar, "––" yok)."""
    ana = tz.fihrist_ayristir(metin, url, gun)
    govde = re.sub(r"<script.*?</script>|<style.*?</style>", " ", metin or "", flags=re.S | re.I)
    bolum = ""
    kanunlar: List[Dict[str, str]] = []
    for _etiket, _ozn, ic in tz._OGE.findall(govde):
        yazi = tz._duz(ic)
        if not yazi or yazi.startswith(_TIRE):
            continue
        if yazi.isupper() and len(yazi) < 80 and "GAZETE" not in yazi:
            bolum = yazi
        elif TURLER["kanun"]["bolum"].match(katla(bolum)) and _KANUN_KALEMI.match(yazi):
            b = tz._BAGLANTI.search(ic)
            kanunlar.append({"bolum": bolum, "baslik": yazi,
                             "url": tz.rg_normal(urljoin(url, _html.unescape(b.group(1)))) if b else ""})
    bilinen = {k["url"] for k in ana["kalemler"] if k.get("url")}
    kalemler = [k for k in kanunlar if not (k["url"] and k["url"] in bilinen)] + ana["kalemler"]
    return {"sayi": ana["sayi"], "kalemler": kalemler, "mukerrerler": ana["mukerrerler"]}


def tur_bul(kalem: Dict[str, str]) -> Optional[str]:
    fb, fk = katla(kalem.get("bolum")), katla(kalem.get("baslik"))
    if IBK.search(fk) or IBK.search(fb):
        return "ibk"
    for kod, t in TURLER.items():
        if t["bolum"] is not None and t["bolum"].match(fb):
            return kod
    return None


def gurultu_mu(kalem: Dict[str, str], tur: str) -> bool:
    return tur in ("yonetmelik", "cb_karari") and bool(GURULTU.search(katla(kalem.get("baslik"))))


def tara(gunler: List[date], getir: Callable[..., Tuple[int, bytes]]) -> Dict[str, Any]:
    """Günlerin fihristleri (mükerrer dahil). Hata o günü 'hata' yapar, diğer günler sürer."""
    kayit: Dict[str, Dict[str, Any]] = {}
    kalemler: List[Dict[str, Any]] = []

    def oku(g: date) -> None:
        url = tz.gun_url(g)
        try:
            durum, govde = getir(url, FIHRIST_ZAMAN_ASIMI)
            if durum == 404:
                kayit[g.isoformat()] = {"durum": "yok", "url": url}
                return
            if durum != 200:
                raise ortak.ErisimHatasi("HTTP %d: %s" % (durum, url))
            f = fihrist(tz._coz(govde), url, g)
            hepsi = [dict(k, mukerrer=None) for k in f["kalemler"]]
            for n in f["mukerrerler"]:
                murl = tz.gun_url(g, n)
                d2, g2 = getir(murl, FIHRIST_ZAMAN_ASIMI)
                if d2 != 200:
                    raise ortak.ErisimHatasi("HTTP %d: %s" % (d2, murl))
                hepsi += [dict(k, mukerrer=n) for k in fihrist(tz._coz(g2), murl, g)["kalemler"]]
            for k in hepsi:
                k.update(tarih=g.isoformat(), sayi=f["sayi"])
            kalemler.extend(hepsi)
            kayit[g.isoformat()] = {"durum": "tamam", "sayi": f["sayi"], "mukerrerler": f["mukerrerler"],
                                    "kalem": len(hepsi), "url": url}
        except (ortak.ErisimHatasi, tz.ErisimHatasi) as exc:
            kayit[g.isoformat()] = {"durum": "hata", "neden": str(exc), "url": url}

    for g in gunler:
        oku(g)
    for g in gunler:                                  # zaman aşımı çoğu kez geçicidir: sonda bir kez daha
        if kayit[g.isoformat()]["durum"] == "hata" and not (hasattr(getir, "neden") and getir.neden()):
            oku(g)
    hatalar = ["%s: %s" % (ortak.tr_tarih(g), k["neden"]) for g, k in sorted(kayit.items()) if k["durum"] == "hata"]
    if kayit and all(k["durum"] == "yok" for k in kayit.values()):
        hatalar.append("Taranan günlerin hiçbirinde Resmî Gazete fihristi bulunamadı (HTTP 404)")
    return {"gunler": kayit, "kalemler": kalemler, "hatalar": hatalar}


# --------------------------------------------------------------------------- metin
def pdf_metni(govde: bytes) -> Tuple[Optional[str], str]:
    try:
        import pypdf  # iş akışında kurulur; yoksa başlıkla yetinilir
    except ImportError:
        return None, "PDF okunamadı (pypdf kurulu değil); yalnız başlıkla arandı"
    try:
        okuyucu = pypdf.PdfReader(io.BytesIO(govde))
        metin = " ".join((s.extract_text() or "") for s in okuyucu.pages[:PDF_SAYFA])
    except Exception as exc:  # noqa: BLE001 - bozuk PDF koşuyu düşürmez
        return None, "PDF okunamadı (%s); yalnız başlıkla arandı" % type(exc).__name__
    metin = " ".join(metin.split())
    return (metin, "okundu") if metin else (None, "PDF'te metin katmanı yok; yalnız başlıkla arandı")


def metin_getir(url: str, getir: Callable[..., Tuple[int, bytes]]) -> Tuple[Optional[str], str]:
    if not url:
        return None, "bağlantı yok; yalnız başlıkla arandı"
    try:
        durum, govde = getir(url, METIN_ZAMAN_ASIMI)
    except (ortak.ErisimHatasi, tz.ErisimHatasi) as exc:
        return None, "okunamadı (%s); yalnız başlıkla arandı" % exc
    if durum != 200:
        return None, "okunamadı (HTTP %d); yalnız başlıkla arandı" % durum
    if url.lower().endswith(".pdf"):
        return pdf_metni(govde)
    govde_m = re.sub(r"<script.*?</script>|<style.*?</style>", " ", tz._coz(govde), flags=re.S | re.I)
    return ortak.duz(govde_m), "okundu"


# --------------------------------------------------------------------------- atıflar
_ATIF = re.compile(
    r"(?<![\d/.,])(\d{1,5})\s+sayılı\s+((?:[^\s;:()“”\"]+\s+){0,16}?)"
    r"(kanun\s+hükmünde\s+kararname\w*|cumhurbaşkanlığı\s+kararname\w*|cumhurbaşkanı\s+karar\w*|kanun\w*|karar\w*|"
    r"yönetmeli\w*)", re.I)


def _atif_turu(bas: str) -> str:
    k = katla(bas)
    if k.startswith("kanun hukmunde"):
        return "khk"
    if k.startswith("cumhurbaskanligi kararname"):
        return "cbk"
    if k.startswith("kanun"):
        return "kanun"
    if k.startswith("yonetmeli"):
        return "yonetmelik"
    return "karar"


def atiflar(metin: str) -> List[Dict[str, Any]]:
    """'N sayılı … Kanunu/Kararnamesi/Kararı' atıfları (ilk görülme sırasıyla, tekil)."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for m in _ATIF.finditer(" ".join((metin or "").split())):
        tur = _atif_turu(m.group(3))
        anahtar = (tur, m.group(1))
        orta = m.group(2).strip()
        if anahtar not in out:
            out[anahtar] = {"tur": tur, "no": m.group(1), "orta": orta, "sayi": 0}
        elif orta and not out[anahtar]["orta"]:
            out[anahtar]["orta"] = orta
        out[anahtar]["sayi"] += 1
    return list(out.values())


def _ek_at(sozcuk: str) -> str:
    """Bulunma ve tamlama ekini atar: Yönetmeliğinde → Yönetmeliği, Kararda → Karar, Kararnamenin → Kararname."""
    for ek in ("nde", "nda", "nin", "nın", "nun", "nün"):
        if sozcuk.endswith(ek) and len(sozcuk) > len(ek) + 3:
            return sozcuk[: -len(ek)]
    for ek in ("de", "da", "te", "ta", "in", "ın", "un", "ün"):
        if sozcuk.endswith(ek) and len(sozcuk) > len(ek) + 3:
            return sozcuk[: -len(ek)]
    return sozcuk


_DEGISIKLIK = re.compile(r"\s+(değişiklik yapılmasına|yürürlükten kaldırılmasına)\s+(dair|ilişkin)\b", re.I)
_KARAR_SAYISI = re.compile(r"\s*\((karar sayısı|kararname numarası|karar)\s*:[^)]*\)\s*$", re.I)


def degisen_ad(baslik: str) -> str:
    """Başlıktan değişen (ya da yeni çıkan) düzenlemenin adı: 'Noterlik Kanunu Yönetmeliğinde Değişiklik
    Yapılmasına Dair Yönetmelik' → 'Noterlik Kanunu Yönetmeliği'."""
    b = _KARAR_SAYISI.sub("", " ".join((baslik or "").split())).lstrip("–—- ").strip()
    m = _DEGISIKLIK.search(b)
    if not m:
        return b
    on = re.split(r"[’']", b[: m.start()])[0].strip()
    parca = on.split()
    if not parca:
        return b
    return " ".join(parca[:-1] + [_ek_at(parca[-1])])


_KOKLER = ("yonetmeli", "kararname", "karar", "esas", "tarife", "tebli", "yonerge", "kanun")


def _ad_koku(ad: str) -> str:
    """Katlanmış ad; son sözcük köke indirilir (ekli biçimler de eşleşsin)."""
    s = katla(ad).split()
    if not s:
        return ""
    son = s[-1]
    for kok in _KOKLER:
        if son.startswith(kok):
            son = kok
            break
    else:
        son = son[: max(4, len(son) - 3)]
    return " ".join(s[:-1] + [son])


# --------------------------------------------------------------------------- kalıplar
def _sinirli(kisa: str, m_ile: bool = False) -> Pattern:
    son = r"\s*(?:m\.|md\.)" if m_ile else r"(?![%s])" % _HARF
    return re.compile(r"(?<![%s])%s%s" % (_HARF, re.escape(kisa), son))


def no_kalibi(no: str) -> Pattern:
    """Kanun numarası: '6100 sayılı', '2872 m. 25', '6446 EPK', 'Kanun No. 6446'."""
    return re.compile(
        r"(?<![\d.,/])%s(?:\s*sayılı|\s*s\.|\s*sy\.|\s*m\.|\s*md\.|\s+kanun|\s+K\.|\s+İş\s+K\.|"
        r"\s+(?-i:[A-ZÇĞİÖŞÜ]{2,6})(?![%s]))|(?:kanun|law)\s*no\.?\s*:?\s*%s(?!\d)" % (no, _HARF, no), re.I)


def ad_kalibi(ad: str) -> Optional[Pattern]:
    kok = _ad_koku(ad)
    if len(kok) < 10 or len(kok.split()) < 2:
        return None
    return re.compile(r"(?<![a-z0-9])%s" % re.escape(kok))


def _ek_kalip(tur: str, no: str) -> List[Tuple[str, Pattern, bool]]:
    if tur == "cbk":
        return [("%s sayılı CBK" % no, re.compile(
            r"(?<![\d.,/])%s\s*sayılı\s*(?:CBK|cumhurbaşkanlığı\s+kararname)|CBK\s*(?:no\.?|numaralı)\s*:?\s*%s(?!\d)"
            % (no, no), re.I), False)]
    if tur == "khk":
        return [("%s sayılı KHK" % no, re.compile(
            r"(?<![\d.,/])%s\s*sayılı\s*(?:KHK|kanun\s+hükmünde)|KHK\s*(?:no\.?)?\s*:?\s*%s(?!\d)" % (no, no), re.I),
            False)]
    return [("%s sayılı Karar" % no, re.compile(
        r"(?<![\d.,/])%s\s*sayılı\s*(?:karar|cumhurbaşkanı\s+kararı|BKK)" % no, re.I), False)]


def tablo_yukle(yol: str = TABLO_YOLU) -> Dict[str, Any]:
    with open(yol, encoding="utf-8") as fh:
        veri = json.load(fh)
    return {"kanunlar": {k["no"]: k for k in veri["kanunlar"]}, "genel_dayanak": set(veri.get("genel_dayanak", []))}


def kanun_terimleri(no: str, ad: str, tablo: Dict[str, Any]) -> List[Tuple[str, Pattern, bool]]:
    """(etiket, kalıp, katlanmış metinde mi) — numara, tablo kısaltmaları, tablo adı ve RG metnindeki ad."""
    t: List[Tuple[str, Pattern, bool]] = [(no, no_kalibi(no), False)]
    k = tablo["kanunlar"].get(no)
    adlar = []
    if k:
        t += [(s, _sinirli(s), False) for s in k.get("kisa", [])]
        t += [(s + " m.", _sinirli(s, True), False) for s in k.get("kisa_m", [])]
        adlar.append(k["ad"])
    if ad:
        adlar.append(ad)
    gorulen = set()
    for a in adlar:
        kalip = ad_kalibi(a)
        if kalip is not None and kalip.pattern not in gorulen:
            gorulen.add(kalip.pattern)
            t.append((a, kalip, True))
    return t


def metinde_kisaltmalar(metin: str, tablo: Dict[str, Any]) -> List[str]:
    """Metinde kısaltması ya da adı geçen tablo kanunları (içtihadı birleştirme kararları 'HMK m. 353' yazar)."""
    katli = katla(metin)
    out = []
    for no, k in tablo["kanunlar"].items():
        bulundu = any(_sinirli(s).search(metin) for s in k.get("kisa", []))
        bulundu = bulundu or any(_sinirli(s, True).search(metin) for s in k.get("kisa_m", []))
        kalip = ad_kalibi(k["ad"])
        bulundu = bulundu or bool(kalip and kalip.search(katli))
        if bulundu:
            out.append(no)
    return out


# --------------------------------------------------------------------------- paketler
_PAKET = re.compile(r"^ArthurLegal-(.+?)-v[\d.]+-Public-Release$")


class Paketler:
    """`ArthurLegal-*-Public-Release/knowledge/` altındaki rehberler (md, json, txt)."""

    def __init__(self, kok: str) -> None:
        self.dosyalar: List[Tuple[str, str, str, str]] = []
        self._bellek: Dict[Tuple[str, int, bool], List[Tuple[str, str, int]]] = {}
        for yol in sorted(glob.glob(os.path.join(kok, "ArthurLegal-*-Public-Release", "knowledge", "**", "*"),
                                    recursive=True)):
            if not os.path.isfile(yol) or not yol.lower().endswith((".md", ".json", ".txt")):
                continue
            goreli = os.path.relpath(yol, kok).replace(os.sep, "/")
            ust, _, ic = goreli.partition("/knowledge/")
            m = _PAKET.match(ust)
            with open(yol, encoding="utf-8", errors="replace") as fh:
                metin = fh.read()
            self.dosyalar.append((m.group(1) if m else ust, ic, metin, katla(metin)))

    def _say(self, kalip: Pattern, katli_mi: bool) -> List[Tuple[str, str, int]]:
        anahtar = (kalip.pattern, kalip.flags, katli_mi)
        if anahtar not in self._bellek:   # aynı kanun haftada birçok yayımda geçer (KVKK, HMK)
            self._bellek[anahtar] = [(p, y, n) for p, y, m, k in self.dosyalar
                                     for n in [len(kalip.findall(k if katli_mi else m))] if n]
        return self._bellek[anahtar]

    def ara(self, terimler: List[Tuple[str, Pattern, bool]]) -> Dict[str, Dict[str, Any]]:
        """Rehber yolu → {paketler, terimler: {etiket: sayı}} (aynı rehber birden çok pakette: en büyük sayı)."""
        out: Dict[str, Dict[str, Any]] = {}
        for etiket, kalip, katli_mi in terimler:
            for paket, yol, n in self._say(kalip, katli_mi):
                r = out.setdefault(yol, {"paketler": [], "terimler": {}})
                if paket not in r["paketler"]:
                    r["paketler"].append(paket)
                r["terimler"][etiket] = max(r["terimler"].get(etiket, 0), n)
        return out


# --------------------------------------------------------------------------- değerlendirme
def _kanun_adi(a: Dict[str, Any], tablo: Dict[str, Any]) -> str:
    k = tablo["kanunlar"].get(a["no"])
    if k:
        return k["ad"]
    orta = a.get("orta") or ""
    if a["tur"] == "kanun":
        return (orta + " Kanunu").strip() if orta and not katla(orta).endswith("hakkinda") else (orta + " Kanun").strip()
    if a["tur"] == "khk":
        return (orta + " Kanun Hükmünde Kararname").strip()
    if a["tur"] == "cbk":
        return (orta + " Cumhurbaşkanlığı Kararnamesi").strip()
    return (orta + " Karar").strip()


def degerlendir(kalem: Dict[str, Any], metin: Optional[str], paketler: Paketler,
                tablo: Dict[str, Any]) -> Dict[str, Any]:
    """Yayımın dokunduğu mevzuat ve onlara atıf yapan rehberler."""
    tur = kalem["tur"]
    kaynak = "%s %s" % (kalem["baslik"], metin or "")
    dokunulan: List[Dict[str, Any]] = []

    def ekle(tur_: str, no: str, ad: str, nasil: str, terimler: List[Tuple[str, Pattern, bool]]) -> None:
        if any(d["tur"] == tur_ and d["no"] == no and d["no"] for d in dokunulan):
            return
        dokunulan.append({"tur": tur_, "no": no, "ad": ad, "nasil": nasil, "terimler": terimler})

    if tur in ("yonetmelik", "cb_karari"):
        ad = degisen_ad(kalem["baslik"])
        kalip = ad_kalibi(ad)
        if kalip is not None:
            ekle("ad", "", ad, "adıyla", [(ad, kalip, True)])
    if tur == "kanun":
        m = _KANUN_KALEMI.match(kalem["baslik"])
        if m:
            ekle("kanun", m.group(1), m.group(2), "yayımlanan kanun", kanun_terimleri(m.group(1), "", tablo))
    for a in atiflar(kaynak):
        if a["tur"] == "yonetmelik":
            continue
        if tur in ("yonetmelik", "cb_karari"):
            if a["tur"] == "cbk" or (a["tur"] in ("kanun", "khk") and a["no"] in tablo["genel_dayanak"]):
                continue
            nasil = "atıf (dayanak)" if a["tur"] in ("kanun", "khk") else "atıf"
        else:
            nasil = "metinde atıf"
        ad = _kanun_adi(a, tablo)
        if a["tur"] == "kanun":
            ekle("kanun", a["no"], ad, nasil, kanun_terimleri(a["no"], ad, tablo))
        else:
            ekle(a["tur"], a["no"], ad, nasil, _ek_kalip(a["tur"], a["no"]))
    if tur == "ibk" or not metin:
        for no in metinde_kisaltmalar(kaynak, tablo):
            ekle("kanun", no, tablo["kanunlar"][no]["ad"], "metinde kısaltma ya da ad",
                 kanun_terimleri(no, "", tablo))

    rehberler: Dict[str, Dict[str, Any]] = {}
    for d in dokunulan:
        bulunan = paketler.ara(d["terimler"])
        d["rehber"] = len(bulunan)
        d["rehberler"] = bulunan
        for yol, r in bulunan.items():
            hedef = rehberler.setdefault(yol, {"paketler": [], "terimler": {}})
            for p in r["paketler"]:
                if p not in hedef["paketler"]:
                    hedef["paketler"].append(p)
            for e, n in r["terimler"].items():
                hedef["terimler"][e] = max(hedef["terimler"].get(e, 0), n)
    return {"dokunulan": [{k: v for k, v in d.items() if k != "terimler"} for d in dokunulan],
            "rehberler": rehberler}


# --------------------------------------------------------------------------- issue
def issue_basligi(k: Dict[str, Any]) -> str:
    return "Mevzuat değişikliği: %s, RG %s: %s" % (TURLER[k["tur"]]["ad"], ortak.tr_tarih(k["tarih"]),
                                                  ortak.kisalt(k["baslik"], 170))


def _kunye(k: Dict[str, Any]) -> str:
    return "RG %s, S. %s%s" % (ortak.tr_tarih(k["tarih"]), k.get("sayi") or "?",
                               " (%d. Mükerrer)" % k["mukerrer"] if k.get("mukerrer") else "")


def issue_govdesi(k: Dict[str, Any]) -> str:
    s = ["Haftalık Resmî Gazete taraması bu yayımı buldu (kalite süreci 7).", "",
         "- Tür: %s" % TURLER[k["tur"]]["ad"],
         "- Künye: %s" % _kunye(k),
         "- Başlık: %s" % k["baslik"],
         "- Metin: %s" % (k.get("url") or "-"),
         "- Metin okuma: %s" % k.get("metin_durumu", "-")]
    if k["rehberler"] and all(d["nasil"] == "atıf (dayanak)" for d in k["dokunulan"] if d.get("rehber")):
        s.append("- Eşleşme yalnız dayanak kanunundan: düzenleme rehberlerdeki konuya dokunmuyor olabilir, önce "
                 "başlığa bakın.")
    s += ["", "## Dokunduğu mevzuat", ""]
    if k["dokunulan"]:
        for d in k["dokunulan"]:
            ad = "%s sayılı %s" % (d["no"], d["ad"]) if d["no"] else d["ad"]
            s.append("- %s (%s): %s" % (ad, d["nasil"], "%d rehberde geçiyor" % d["rehber"] if d["rehber"]
                                        else "rehberlerde geçmiyor"))
    else:
        s.append("- Metinden çıkarılamadı; başlığa ve metne bakın.")
    s += ["", "## Etkilenebilecek rehberler", ""]
    orta: List[str] = []
    if k["rehberler"]:
        s.append("Paketler: `ArthurLegal-*-Public-Release/knowledge/` (beerbottle90/ArthurLegal, main). Her mevzuat "
                 "için en çok geçtiği %d rehber; toplam %d rehber." % (TABLO_SATIRI, len(k["rehberler"])))
        for d in k["dokunulan"]:
            if not d.get("rehberler"):
                continue
            ad = "%s sayılı %s" % (d["no"], d["ad"]) if d["no"] else d["ad"]
            sirali = sorted(d["rehberler"].items(), key=lambda x: (-sum(x[1]["terimler"].values()), x[0]))
            orta += ["", "### %s: %d rehber" % (ortak.kisalt(ad, 120), len(sirali)), "",
                     "| Rehber | Paket | Geçen ifade (sayı) |", "|---|---|---|"]
            for yol, r in sirali[:TABLO_SATIRI]:
                terim = ", ".join("%s ×%d" % (e, n) for e, n in sorted(r["terimler"].items(), key=lambda x: -x[1]))
                orta.append("| `%s` | %s | %s |" % (yol, ", ".join(r["paketler"]),
                                                    ortak.kisalt(terim, 120).replace("|", "/")))
            if len(sirali) > TABLO_SATIRI:
                orta.append("| … | | %d rehber daha |" % (len(sirali) - TABLO_SATIRI))
    else:
        s.append("Paketlerde bu mevzuata atıf bulunamadı. Yeni bir konu olabilir: rehber gerekip gerekmediğine bakın.")
    son = ["", "## Aylık bakım saatinde", "",
          "- [ ] Değişikliği resmî metinden okuyun (yürürlük tarihi, geçiş hükümleri).",
          "- [ ] Listelenen rehberlerde etkilenen bölümleri bulun; düzeltme gerekiyorsa ArthurLegal'de PR açın "
          "(madde metnini `tr_mevzuat_madde_getir` ile canlı çekerek).",
          "- [ ] Rehber etkilenmiyorsa issue'yu gerekçesiyle kapatın.",
          "",
          "Eşleşme numara, kısaltma ve ada göre yapılır; yanlış eşleşme olabilir, eksik de olabilir. "
          "Bu issue'yu mevzuat bekçisi açtı (`.github/workflows/mevzuat-bekcisi.yml`); aynı yayım için yenisi açılmaz."]
    sabit = len("\n".join(s + son)) + 200
    if sabit + len("\n".join(orta)) > GOVDE_AZAMI:          # çok kanuna dokunan torba kanun
        while orta and sabit + len("\n".join(orta)) > GOVDE_AZAMI:
            orta = orta[:-20]
        orta += ["", "… Tablolar GitHub'ın issue sınırı yüzünden kesildi; tam liste koşunun `mevzuat-bekcisi-sonuc` "
                     "ekinde (Actions, 35 gün)."]
    return "\n".join(s + orta + son)[:GOVDE_AZAMI + 1000]


def tarama_uyarisi(hatalar: List[str]) -> str:
    return "\n".join([
        "Haftalık mevzuat bekçisi Resmî Gazete taramasını tamamlayamadı; aşağıdaki günlerdeki yayımlar "
        "denetlenmedi.", ""] + ["- %s" % h for h in hatalar] + [
        "",
        "Yapılacak: Resmî Gazete'yi bir tarayıcıdan açıp sitenin çalıştığını görün; sonra Actions'tan "
        "\"Mevzuat bekçisi\" işini `gun` girdisiyle (en çok 14) elle çalıştırın. Sorun sürüyorsa iş akışının "
        "günlüğüne bakın. Taranamayan haftayı elle kapatınca bu issue'yu kapatın."])


def paket_uyarisi(kok: str) -> str:
    return "\n".join([
        "Mevzuat bekçisi ArthurLegal paketlerinde rehber bulamadı (`%s/ArthurLegal-*-Public-Release/knowledge/`)." % kok,
        "", "Değişiklikler yine issue'ya dönüştü ama etkilenebilecek rehber listesi boş kaldı.", "",
        "Yapılacak: beerbottle90/ArthurLegal'de paket klasörlerinin adı ya da yeri değiştiyse "
        "`.github/workflows/mevzuat-bekcisi.yml`'deki sparse-checkout desenini ve `kalite/mevzuat_bekcisi.py`'deki "
        "`Paketler` yolunu güncelleyin."])


# --------------------------------------------------------------------------- akış
def calis(bugun: date, gun: int, paketler: Paketler, getir: Callable[..., Tuple[int, bytes]],
          tablo: Dict[str, Any]) -> Dict[str, Any]:
    gunler = [bugun - timedelta(days=i) for i in range(gun - 1, -1, -1)]
    tarama = tara(gunler, getir)
    secilen: List[Dict[str, Any]] = []
    elenen: List[Dict[str, Any]] = []
    for k in tarama["kalemler"]:
        tur = tur_bul(k)
        if tur is None:
            continue
        k = dict(k, tur=tur)
        (elenen if gurultu_mu(k, tur) else secilen).append(k)
    secilen.sort(key=lambda k: (TURLER[k["tur"]]["sira"], k["tarih"], k["baslik"]))
    for k in secilen:
        neden = getir.neden() if hasattr(getir, "neden") else None
        if neden:
            metin, durum = None, "okunmadı (%s); yalnız başlıkla arandı" % neden
        else:
            metin, durum = metin_getir(k.get("url") or "", getir)
        k["metin_durumu"] = durum
        k.update(degerlendir(k, metin, paketler, tablo))
        her_zaman = TURLER[k["tur"]]["her_zaman"] and not UYGUN_BULMA.search(katla(k["baslik"]))
        k["issue"] = her_zaman or bool(k["rehberler"])
    return {"aralik": [gunler[0].isoformat(), gunler[-1].isoformat()], "gunler": tarama["gunler"],
            "hatalar": tarama["hatalar"], "secilen": secilen, "elenen": elenen,
            "paket_dosyasi": len(paketler.dosyalar), "istek": getattr(getir, "sayi", None)}


def adaylar(sonuc: Dict[str, Any], paket_kok: str) -> List[Dict[str, str]]:
    out = [{"title": issue_basligi(k), "body": issue_govdesi(k), "tur": "degisiklik"}
           for k in sonuc["secilen"] if k["issue"]]
    if sonuc["hatalar"]:
        out.append({"title": UYARI_TARAMA, "body": tarama_uyarisi(sonuc["hatalar"]), "tur": "uyari"})
    if not sonuc["paket_dosyasi"]:
        out.append({"title": UYARI_PAKET, "body": paket_uyarisi(paket_kok), "tur": "uyari"})
    return out


def ozet(sonuc: Dict[str, Any], ac: List[Dict[str, str]], kuru: bool, hata: Optional[str]) -> str:
    s = ["## Mevzuat bekçisi (kalite süreci 7)", "",
         "- Aralık: %s – %s" % tuple(ortak.tr_tarih(x) for x in sonuc["aralik"]),
         "- İstek: %s · paket rehberi: %d" % (sonuc["istek"], sonuc["paket_dosyasi"])]
    for g, k in sorted(sonuc["gunler"].items()):
        s.append("- %s: %s%s" % (ortak.tr_tarih(g), k["durum"], ", sayı %s, mükerrer %s, %s kalem" % (
            k.get("sayi"), k.get("mukerrerler") or "-", k.get("kalem")) if k["durum"] == "tamam"
            else (" (%s)" % k.get("neden") if k.get("neden") else "")))
    s += ["", "### Seçilen yayımlar", ""]
    if not sonuc["secilen"]:
        s.append("- Yok.")
    for k in sonuc["secilen"]:
        s.append("- %s · %s · %s · %s · %d rehber%s" % (
            ortak.tr_tarih(k["tarih"]), TURLER[k["tur"]]["ad"], ortak.kisalt(k["baslik"], 120), k["metin_durumu"],
            len(k["rehberler"]), "" if k["issue"] else " (issue yok: rehber eşleşmedi)"))
    if sonuc["elenen"]:
        s += ["", "### Elenen (yere ya da kuruma özgü): %d" % len(sonuc["elenen"]), ""]
        s += ["- %s · %s" % (ortak.tr_tarih(k["tarih"]), ortak.kisalt(k["baslik"], 120)) for k in sonuc["elenen"]]
    s += ["", "### Issue", ""]
    s += ["- %s: %s" % ("Açılırdı (kuru)" if kuru else "Açıldı", i["title"]) for i in ac] or ["- Yeni issue yok."]
    if hata:
        s.append("- HATA: %s" % hata)
    return "\n".join(s) + "\n"


def main(argv: Optional[List[str]] = None, getir: Optional[Callable[..., Tuple[int, bytes]]] = None,
         uyu: Callable[[float], None] = time.sleep) -> int:
    p = argparse.ArgumentParser(prog="mevzuat_bekcisi.py", description="Kalite süreci 7: mevzuat bekçisi.")
    p.add_argument("--paketler", required=True, help="beerbottle90/ArthurLegal klonu (paket klasörlerinin üstü)")
    p.add_argument("--gun", default=str(GUN_VARSAYILAN), help="bugün dahil kaç gün (1-%d)" % GUN_AZAMI)
    p.add_argument("--bugun", help="YYYY-AA-GG (deneme için)")
    p.add_argument("--cikti", help="sonucu bu JSON dosyasına da yaz")
    p.add_argument("--kuru", action="store_true", help="issue açma, yalnız planı yaz")
    a = p.parse_args(argv)
    gun = int(a.gun) if str(a.gun).strip().isdigit() else GUN_VARSAYILAN
    gun = max(1, min(gun, GUN_AZAMI))
    bugun = date.fromisoformat(a.bugun) if a.bugun else datetime.now(tz.TR).date()
    istemci = ortak.Nazik(getir or tz.http_getir, aralik=ISTEK_ARALIGI, sinir=ISTEK_SINIRI, uyu=uyu,
                          sure=SURE_SINIRI)
    paketler = Paketler(a.paketler)
    sonuc = calis(bugun, gun, paketler, istemci, tablo_yukle())
    ac, hata = ortak.issuelari_ac(adaylar(sonuc, a.paketler), ETIKET, RENK, ETIKET_ACIKLAMA, a.kuru)
    for h in sonuc["hatalar"]:
        ortak.uyari("Mevzuat bekçisi: %s" % h)
    if not sonuc["paket_dosyasi"]:
        ortak.uyari("Mevzuat bekçisi: paket rehberi bulunamadı (%s)" % a.paketler)
    if a.cikti:
        ortak.json_yaz(a.cikti, sonuc)
    ortak.ozet_yaz(ozet(sonuc, ac, a.kuru, hata))
    return 1 if hata else 0


if __name__ == "__main__":
    sys.exit(main())
