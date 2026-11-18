#!/usr/bin/env python3
"""model_bekcisi — kalite süreci 8: model ve platform değişikliği kapısı.

Haftada bir (.github/workflows/model-bekcisi.yml) dört resmî sayfayı okur ve önceki koşuyla karşılaştırır
(önceki koşunun kaydı Actions önbelleğinde, `.bekci/model-platform.json`):

* Claude model listesi (platform.claude.com/docs/en/models/overview.md): güncel modellerin API kimlikleri ve
  "All models" tablosundaki modeller ile durumları (aktif, sınırlı erişim, emekli).
* Claude uygulamaları sürüm notları (Claude Desktop dahil; support.claude.com).
* ChatGPT sürüm notları (help.openai.com): yalnız model, masaüstü uygulaması, MCP, bağlayıcı ve eklenti kayıtları.
* ChatGPT uygulama (MCP) değişiklik günlüğü (developers.openai.com/plugins/changelog; eski adı Apps SDK).

Her değişiklik için "Model/platform değişikliği: …" başlıklı bir issue açar (aylık durum e-postası başlıktan
okur). Aynı başlıklı issue (açık ya da kapalı) varsa yenisi açılmaz. Issue'ya sürüm notunun yalnız başlığı ve
bağlantısı girer, metni kopyalanmaz.

Önceki kayıt yoksa (ilk koşu ya da önbellek silindi): model listesi depodaki tabanla (`taban_modeller.json`),
sürüm notları son 8 günün kayıtlarıyla karşılaştırılır; başlıkla tekilleştirme aynı değişikliği iki kez açmaz.
Bir sayfada 10'dan çok yeni kayıt çıkarsa sayfa yapısı değişmiş sayılır: tek uyarı issue'su açılır.

Sayfa okunamazsa ya da tanınmazsa sessiz geçmez: Actions uyarısı ve "Model/platform bekçisi uyarısı: …" issue'su
(aynı başlıklı açık issue yoksa); o kaynağın önceki kaydı korunur.

    python kalite/model_bekcisi.py --durum .bekci/model-platform.json [--kuru]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

BURADA = os.path.dirname(os.path.abspath(__file__))
if BURADA not in sys.path:
    sys.path.insert(0, BURADA)

import ortak  # noqa: E402

ETIKET = "model-platform-bekcisi"
RENK = "5319E7"
ETIKET_ACIKLAMA = "Kalite süreci 8: model ve platform değişikliği"
ONEK = "Model/platform değişikliği: "
UYARI_ONEK = "Model/platform bekçisi uyarısı: "
TABAN_YOLU = os.path.join(BURADA, "taban_modeller.json")
TAVSIYE = "Minimum Opus 5.5 · Medium / High effort tavsiye ederiz, bağlayıcı değildir."
ILK_KOSU_GUN = 8
YENI_AZAMI = 10
TR = timezone(timedelta(hours=3))

ALANLAR = ("platform.claude.com", "support.claude.com", "help.openai.com", "developers.openai.com")

# ChatGPT sürüm notlarında AL'yi ilgilendiren kayıtlar (başlıkta): model, masaüstü, MCP, bağlayıcı, eklenti.
CHATGPT_ILGILI = re.compile(
    r"\b(mcp|connectors?|plugins?|apps? sdk|developer mode|desktop|windows|macos|mac app|gpt-?\d[\w.]*|o\d|models?|"
    r"retir\w*|deprecat\w*)\b", re.I)

KAYNAKLAR: List[Dict[str, Any]] = [
    {"kod": "claude-modeller", "ad": "Claude model listesi", "tur": "modeller",
     "url": "https://platform.claude.com/docs/en/models/overview.md", "kabul": "text/markdown,*/*;q=0.5"},
    {"kod": "claude-surum", "ad": "Claude uygulamaları sürüm notları", "tur": "notlar",
     "url": "https://support.claude.com/en/articles/12138966-release-notes",
     "basla": "release notes", "kalem": "pb", "suzgec": None},
    {"kod": "chatgpt-surum", "ad": "ChatGPT sürüm notları", "tur": "notlar",
     "url": "https://help.openai.com/en/articles/6825453-chatgpt-release-notes",
     "basla": "release notes", "kalem": "h2", "suzgec": CHATGPT_ILGILI},
    {"kod": "chatgpt-uygulama", "ad": "ChatGPT uygulama (MCP) değişiklik günlüğü", "tur": "notlar",
     "url": "https://developers.openai.com/plugins/changelog",
     "basla": "changelog", "kalem": "h3", "suzgec": None},
]


class OkumaHatasi(Exception):
    """Sayfa okundu ama tanınmadı (yapı değişmiş olabilir)."""


# --------------------------------------------------------------------------- tarih
_AYLAR = {a: i for i, a in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}
_GUN = re.compile(r"^(?:[A-Za-z]+,\s+)?([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$")
_AY = re.compile(r"^([A-Za-z]+)\s+(\d{4})$")


def tarih_coz(metin: str) -> Optional[str]:
    """'November 17, 2026' → '2026-11-17'; 'November 2026' → '2026-11'; değilse None."""
    t = " ".join((metin or "").split())
    m = _GUN.match(t)
    if m and m.group(1).lower() in _AYLAR:
        try:
            return date(int(m.group(3)), _AYLAR[m.group(1).lower()], int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = _AY.match(t)
    if m and m.group(1).lower() in _AYLAR:
        return "%s-%02d" % (m.group(2), _AYLAR[m.group(1).lower()])
    return None


# --------------------------------------------------------------------------- sürüm notları
_BLOK = re.compile(r"<(h[1-4]|p)\b[^>]*>(.*?)</\1\s*>", re.S | re.I)
_KALIN = re.compile(r"<(b|strong)\b[^>]*>(.*?)</\1\s*>", re.S | re.I)


def bloklar(html_metin: str) -> List[Tuple[str, str]]:
    """Belge sırasıyla (tür, metin): tür 'h1'..'h4', 'pb' (yalnız kalın paragraf) ya da 'p'."""
    govde = re.sub(r"<script.*?</script>|<style.*?</style>|<nav\b.*?</nav>", " ", html_metin or "", flags=re.S | re.I)
    out: List[Tuple[str, str]] = []
    for etiket, ic in _BLOK.findall(govde):
        yazi = ortak.duz(ic)
        if not yazi:
            continue
        etiket = etiket.lower()
        if etiket == "p":
            kalin = ortak.duz(" ".join(k for _e, k in _KALIN.findall(ic)))
            etiket = "pb" if kalin and kalin == yazi else "p"
        out.append((etiket, yazi))
    return out


def notlari_ayristir(html_metin: str, kaynak: Dict[str, Any]) -> List[Dict[str, str]]:
    """Sürüm notu kayıtları: [{tarih, baslik}] (sayfadaki sırayla). Tanınmazsa OkumaHatasi."""
    basladi = False
    dis_seviye: Optional[int] = None
    tarih: Optional[str] = None
    tarih_kalemi = 0
    ilk_paragraf: Optional[str] = None
    out: List[Dict[str, str]] = []

    def tarihi_kapat() -> None:
        if tarih and kaynak["kalem"] == "pb" and not tarih_kalemi and ilk_paragraf:
            out.append({"tarih": tarih, "baslik": ortak.kisalt(ilk_paragraf, 120)})

    for tur, yazi in bloklar(html_metin):
        if not basladi:
            if tur == "h1" and kaynak["basla"] in yazi.lower():
                basladi = True
            continue
        seviye = int(tur[1]) if tur.startswith("h") else None
        t = tarih_coz(yazi) if seviye else None
        if t:
            tarihi_kapat()
            dis_seviye = seviye if dis_seviye is None else min(dis_seviye, seviye)
            tarih, tarih_kalemi, ilk_paragraf = t, 0, None
            continue
        if seviye is not None and dis_seviye is not None and seviye <= dis_seviye:
            break                                    # tarihli bölüm bitti (ör. "Docs agent", "Related articles")
        if tarih is None:
            continue
        if tur == kaynak["kalem"]:
            out.append({"tarih": tarih, "baslik": ortak.kisalt(yazi, 160)})
            tarih_kalemi += 1
        elif tur == "p" and ilk_paragraf is None:
            ilk_paragraf = yazi
    tarihi_kapat()
    if not basladi:
        raise OkumaHatasi("başlangıç başlığı bulunamadı ('%s')" % kaynak["basla"])
    if not out:
        raise OkumaHatasi("tarihli kayıt bulunamadı")
    return out


def kayit_anahtari(k: Dict[str, str]) -> str:
    return "%s|%s" % (k["tarih"], k["baslik"])


def _yakin_mi(tarih: str, bugun: date) -> bool:
    if len(tarih) == 7:                              # yalnız ay: bu ay
        return tarih == bugun.strftime("%Y-%m")
    try:
        return date.fromisoformat(tarih) >= bugun - timedelta(days=ILK_KOSU_GUN)
    except ValueError:
        return False


def yeni_notlar(kayitlar: List[Dict[str, str]], onceki: Optional[List[str]], bugun: date) -> List[Dict[str, str]]:
    """Önceki koşuda olmayan kayıtlar; önceki kayıt yoksa son 8 günün (aylıklarda bu ayın) kayıtları."""
    if onceki is None:
        return [k for k in kayitlar if _yakin_mi(k["tarih"], bugun)]
    eski = set(onceki)
    return [k for k in kayitlar if kayit_anahtari(k) not in eski]


# --------------------------------------------------------------------------- model listesi
_MODEL_ADI = re.compile(r"Claude [A-Z][a-z]+ \d+(?:\.\d+)?")


def modelleri_ayristir(md: str) -> Dict[str, Any]:
    """{'guncel': [API kimlikleri], 'modeller': {ad: durum}}. Tanınmazsa OkumaHatasi."""
    guncel: List[str] = []
    modeller: Dict[str, str] = {}
    bolum = ""
    for satir in (md or "").splitlines():
        if satir.startswith("## "):
            bolum = satir[3:].strip().lower()
            continue
        if not satir.startswith("|"):
            continue
        hucre = [h.strip() for h in satir.strip().strip("|").split("|")]
        if not hucre:
            continue
        if hucre[0].lower() == "claude api id" and not guncel:
            guncel = [x for h in hucre[1:] for x in re.findall(r"`([^`]+)`", h)]
        elif bolum.startswith("all models"):
            m = _MODEL_ADI.search(hucre[0])
            if not m:
                continue
            ilk = hucre[0].lower()
            durum = ("emekli" if "retired" in ilk else "kullanımdan kalkıyor" if "deprecat" in ilk
                     else "sınırlı erişim" if "limited" in ilk else "aktif")
            modeller.setdefault(m.group(0), durum)
    if not guncel:
        raise OkumaHatasi("'Claude API ID' satırı bulunamadı")
    if not modeller:
        raise OkumaHatasi("'All models' tablosunda model bulunamadı")
    return {"guncel": guncel, "modeller": modeller}


def kimlikten_ad(kimlik: str) -> str:
    """'claude-opus-5-6' → 'Claude Opus 5.6' (tarih son eki atılır)."""
    p = [x for x in kimlik.split("-") if not re.fullmatch(r"\d{8}", x)]
    if len(p) < 3 or p[0] != "claude":
        return kimlik
    return "Claude %s %s" % (p[1].capitalize(), ".".join(p[2:]))


def model_olaylari(onceki: Dict[str, Any], simdi: Dict[str, Any]) -> List[Dict[str, str]]:
    """Değişiklik olayları: [{ozet, ayrinti, yeni_model?}]."""
    olay: List[Dict[str, str]] = []
    om, sm = onceki.get("modeller", {}), simdi["modeller"]
    og, sg = list(onceki.get("guncel", [])), simdi["guncel"]
    yeni_adlar = [a for a in sm if a not in om]
    for ad in yeni_adlar:
        guncelde = [k for k in sg if kimlikten_ad(k) == ad]
        olay.append({"ozet": "yeni model %s" % ad, "yeni_model": "1",
                     "ayrinti": "Model listesine eklendi (durum: %s)%s." % (
                         sm[ad], "; güncel modeller arasında, API kimliği `%s`" % guncelde[0] if guncelde else "")})
    for ad in om:
        if ad not in sm:
            olay.append({"ozet": "%s listeden çıktı" % ad, "ayrinti": "Model artık listede yok."})
        elif om[ad] != sm[ad]:
            olay.append({"ozet": "%s durumu %s" % (ad, sm[ad]), "ayrinti": "Durum: %s → %s." % (om[ad], sm[ad])})
    for k in sg:
        if k not in og and kimlikten_ad(k) not in yeni_adlar:
            olay.append({"ozet": "%s güncel modellere girdi" % k, "ayrinti": "Güncel modeller: %s." % ", ".join(sg)})
    for k in og:
        if k not in sg:
            olay.append({"ozet": "%s güncel modellerden çıktı" % k, "ayrinti": "Güncel modeller: %s." % ", ".join(sg)})
    return olay


def taban_yukle(yol: str = TABAN_YOLU) -> Dict[str, Any]:
    veri = ortak.json_oku(yol)
    return {"guncel": veri.get("guncel", []), "modeller": veri.get("modeller", {})}


# --------------------------------------------------------------------------- issue metinleri
def model_issue(kaynak: Dict[str, Any], olay: Dict[str, str], tum: List[Dict[str, str]]) -> Dict[str, str]:
    govde = ["Haftalık model ve platform bekçisi Claude model listesinde değişiklik buldu (kalite süreci 8).", "",
             "- Değişiklik: %s" % olay["ozet"],
             "- Ayrıntı: %s" % olay["ayrinti"],
             "- Kaynak: %s" % kaynak["url"]]
    if len(tum) > 1:
        govde += ["- Bu koşudaki öteki değişiklikler: %s" % "; ".join(o["ozet"] for o in tum if o is not olay)]
    govde += ["", "## Yapılacaklar", "",
              "- [ ] Süreç 3 araştırma disiplini sınavı (yeni model; abonelik kotası, koşudan önce onay).",
              "- [ ] Süreç 4 elle deneme: Windows ve macOS Claude Desktop, ChatGPT masaüstü.",
              "- [ ] AL'nin model tavsiyesini yeniden teyit edin: \"%s\" Değişecekse bütün yüzeylerde birlikte "
              "(masa model kartı, README, kullanıcı rehberi)." % TAVSIYE,
              "- [ ] Etkisi yoksa issue'yu gerekçesiyle kapatın.",
              "", "Bu issue'yu model ve platform bekçisi açtı (`.github/workflows/model-bekcisi.yml`); aynı değişiklik "
              "için yenisi açılmaz."]
    return {"title": ONEK + "%s, %s" % (kaynak["ad"], olay["ozet"]), "body": "\n".join(govde), "tur": "degisiklik"}


def not_issue(kaynak: Dict[str, Any], k: Dict[str, str]) -> Dict[str, str]:
    tarih = ortak.tr_tarih(k["tarih"])
    govde = ["Haftalık model ve platform bekçisi yeni bir sürüm notu kaydı buldu (kalite süreci 8).", "",
             "- Kaynak: %s" % kaynak["ad"],
             "- Tarih: %s" % tarih,
             "- Kayıt başlığı: %s" % k["baslik"],
             "- Sayfa: %s" % kaynak["url"],
             "", "## Yapılacaklar", "",
             "- [ ] Kaydı resmî sayfadan okuyun: AL'yi (MCP sunucuları, masa, kurulum, bağlayıcılar, model seçimi) "
             "etkiliyor mu?",
             "- [ ] Etkiliyorsa süreç 4 elle deneme (Windows, macOS, ChatGPT masaüstü); gerekirse AL'de düzeltme ve "
             "kullanıcı rehberi.",
             "- [ ] Yeni model getiriyorsa süreç 3 sınavı ve model tavsiyesinin teyidi (\"%s\")." % TAVSIYE,
             "- [ ] Etkisi yoksa issue'yu gerekçesiyle kapatın.",
             "", "Bu issue'yu model ve platform bekçisi açtı (`.github/workflows/model-bekcisi.yml`); aynı kayıt için "
             "yenisi açılmaz."]
    return {"title": ONEK + "%s %s, %s" % (kaynak["ad"], tarih, ortak.kisalt(k["baslik"], 150)),
            "body": "\n".join(govde), "tur": "degisiklik"}


def okuma_uyarisi(kaynak: Dict[str, Any], neden: str) -> Dict[str, str]:
    govde = ["Haftalık model ve platform bekçisi bu sayfayı okuyamadı; bu hafta bu kaynaktaki değişiklikler "
             "denetlenmedi.", "",
             "- Kaynak: %s" % kaynak["ad"],
             "- Sayfa: %s" % kaynak["url"],
             "- Neden: %s" % neden,
             "", "Yapılacak: sayfayı bir tarayıcıdan açın. Taşındıysa ya da yapısı değiştiyse "
             "`kalite/model_bekcisi.py`'deki `KAYNAKLAR`'ı ve ayrıştırıcıyı güncelleyin (testler: "
             "`tests/test_kalite_bekcileri.py`). GitHub'ın makinelerinden erişim engelleniyorsa bu kaynağı elle "
             "izleyin. Düzelince issue'yu kapatın; önceki kayıt korunduğu için sonraki koşu kaçan değişikliği bulur."]
    if "403" in neden:
        govde += ["", "HTTP 403: site otomatik istemcilere doğrulama (ör. Cloudflare) uyguluyor olabilir. Bekçi bunu "
                  "aşmaya çalışmaz; bu kaynağı aylık bakım saatinde tarayıcıdan okuyun."]
    return {"title": UYARI_ONEK + "%s okunamadı" % kaynak["ad"], "body": "\n".join(govde), "tur": "uyari"}


def cok_yeni_uyarisi(kaynak: Dict[str, Any], sayi: int) -> Dict[str, str]:
    govde = ["%s sayfasında bir haftada %d yeni kayıt göründü; sayfa yapısı değişmiş olabilir. Tek tek issue "
             "açılmadı, yeni durum kaydedildi." % (kaynak["ad"], sayi), "",
             "- Sayfa: %s" % kaynak["url"], "",
             "Yapılacak: sayfadaki son kayıtlara elle bakın; ayrıştırıcı yanlış okuyorsa `kalite/model_bekcisi.py`'yi "
             "düzeltin."]
    return {"title": UYARI_ONEK + "%s beklenmedik ölçüde değişti" % kaynak["ad"], "body": "\n".join(govde),
            "tur": "uyari"}


# --------------------------------------------------------------------------- akış
def _coz(govde: bytes) -> str:
    try:
        return govde.decode("utf-8")
    except UnicodeDecodeError:
        return govde.decode("cp1252", errors="replace")


def kaynak_oku(kaynak: Dict[str, Any], getir: Callable[..., Tuple[int, bytes]]) -> Dict[str, Any]:
    """Kaynağın şimdiki kaydı. Ağ hatası ortak.ErisimHatasi, tanınmayan sayfa OkumaHatasi."""
    durum, govde = getir(kaynak["url"], ortak.ZAMAN_ASIMI, kabul=kaynak.get("kabul", ortak.KABUL))
    if durum != 200:
        raise ortak.ErisimHatasi("HTTP %d" % durum)
    metin = _coz(govde)
    if kaynak["tur"] == "modeller":
        return modelleri_ayristir(metin)
    return {"kayitlar": notlari_ayristir(metin, kaynak)}


def calis(durum: Dict[str, Any], getir: Callable[..., Tuple[int, bytes]], bugun: date,
          taban: Dict[str, Any]) -> Dict[str, Any]:
    """Kaynakları okur; issue adayları, yeni durum ve özet satırları döner."""
    adaylar: List[Dict[str, str]] = []
    yeni_durum: Dict[str, Any] = {}
    satirlar: List[str] = []
    uyarilar: List[str] = []
    for kaynak in KAYNAKLAR:
        onceki = durum.get(kaynak["kod"])
        try:
            simdi = kaynak_oku(kaynak, getir)
        except (ortak.ErisimHatasi, OkumaHatasi) as exc:
            neden = "%s: %s" % ("okunamadı" if isinstance(exc, ortak.ErisimHatasi) else "tanınmadı", exc)
            uyarilar.append("%s %s" % (kaynak["ad"], neden))
            adaylar.append(okuma_uyarisi(kaynak, neden))
            if onceki is not None:
                yeni_durum[kaynak["kod"]] = onceki          # kayıt korunur: sonraki koşu kaçanı bulur
            satirlar.append("- %s: UYARI, %s" % (kaynak["ad"], neden))
            continue
        simdi["zaman"] = datetime.now(TR).isoformat(timespec="minutes")
        yeni_durum[kaynak["kod"]] = simdi
        if kaynak["tur"] == "modeller":
            olaylar = model_olaylari(onceki if onceki is not None else taban, simdi)
            adaylar += [model_issue(kaynak, o, olaylar) for o in olaylar]
            satirlar.append("- %s: %d model, güncel: %s%s; %s" % (
                kaynak["ad"], len(simdi["modeller"]), ", ".join(simdi["guncel"]),
                "" if onceki is not None else " (önceki kayıt yok: depodaki tabanla karşılaştırıldı)",
                "; ".join(o["ozet"] for o in olaylar) or "değişiklik yok"))
            continue
        kayitlar = simdi["kayitlar"]
        simdi["kayitlar"] = [kayit_anahtari(k) for k in kayitlar]
        yeniler = yeni_notlar(kayitlar, onceki.get("kayitlar") if onceki else None, bugun)
        suzgec = kaynak.get("suzgec")
        ilgili = [k for k in yeniler if suzgec is None or suzgec.search(k["baslik"])]
        ilgisiz = [k for k in yeniler if k not in ilgili]
        if len(yeniler) > YENI_AZAMI:
            adaylar.append(cok_yeni_uyarisi(kaynak, len(yeniler)))
            uyarilar.append("%s: %d yeni kayıt (yapı değişmiş olabilir)" % (kaynak["ad"], len(yeniler)))
        else:
            adaylar += [not_issue(kaynak, k) for k in ilgili]
        satirlar.append("- %s: %d kayıt okundu, %d yeni%s%s" % (
            kaynak["ad"], len(kayitlar), len(yeniler),
            "" if onceki is not None else " (önceki kayıt yok: son %d gün)" % ILK_KOSU_GUN,
            "; ilgisiz sayılan: %s" % "; ".join(ortak.kisalt(k["baslik"], 80) for k in ilgisiz) if ilgisiz else ""))
    return {"adaylar": adaylar, "durum": yeni_durum, "satirlar": satirlar, "uyarilar": uyarilar}


def main(argv: Optional[List[str]] = None, getir: Optional[Callable[..., Tuple[int, bytes]]] = None,
         bugun: Optional[date] = None, uyu: Callable[[float], None] = time.sleep) -> int:
    p = argparse.ArgumentParser(prog="model_bekcisi.py", description="Kalite süreci 8: model ve platform bekçisi.")
    p.add_argument("--durum", required=True, help="önceki koşunun kaydı (Actions önbelleği)")
    p.add_argument("--kuru", action="store_true", help="issue açma, kaydı yazma; yalnız planı yaz")
    a = p.parse_args(argv)
    istemci = ortak.Nazik(getir or ortak.http_getirici(ALANLAR), aralik=2.0, sinir=10, uyu=uyu)
    sonuc = calis(ortak.json_oku(a.durum), istemci, bugun or datetime.now(TR).date(), taban_yukle())
    ac, hata = ortak.issuelari_ac(sonuc["adaylar"], ETIKET, RENK, ETIKET_ACIKLAMA, a.kuru)
    for u in sonuc["uyarilar"]:
        ortak.uyari("Model/platform bekçisi: %s" % u)
    if not a.kuru and not hata:
        ortak.json_yaz(a.durum, sonuc["durum"])
    s = ["## Model ve platform bekçisi (kalite süreci 8)", ""] + sonuc["satirlar"] + ["", "### Issue", ""]
    s += ["- %s: %s" % ("Açılırdı (kuru)" if a.kuru else "Açıldı", i["title"]) for i in ac] or ["- Yeni issue yok."]
    if hata:
        s.append("- HATA: %s" % hata)
    ortak.ozet_yaz("\n".join(s) + "\n")
    return 1 if hata else 0


if __name__ == "__main__":
    sys.exit(main())
