"""tkgm_kaynak — verinin nereden geldiği ve dosyanın nereye gittiği.

İki ayrı sınır burada çizilir:

1. **Veri kaynağı.** Bugünkü canlı sorgu `tkgm_canli`dedir: Parsel Sorgu'nun herkese açık
   verisi, tek sıra ve dakikada en çok 30 istekle (docs/MANIFESTO.md). `canli_durum`
   ayrı bir kapıdır: TKGM ile resmî bir veri paylaşım kanalı kurulursa (izin belgesi ve
   TKGM'nin verdiği servis adresi) o kanal buraya bağlanır. Gövdesi, o kanalın servis
   sözleşmesine göre yazılacaktır.

2. **Dosya sistemi sınırı.** Birleşik uç (Fly) kimlik doğrulamasızdır. Orada
   yol alan bir araç, sunucunun diskini internete açar. Dosya okuma/yazma
   yalnız stdio taşımasında (kullanıcının kendi makinesi) açıktır.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from typing import Any, Dict, List

_UZANTILAR = (".geojson", ".json", ".kml")
_AZAMI_BOYUT = 5_000_000
_TR = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iisSgGuUoOcC")


class ErisimHatasi(Exception):
    """İstenen dosya işlemi bu çalışma kipinde yapılamaz."""


def paylasilan_mi() -> bool:
    """Taşıma HTTP mi: kimlik doğrulamasız, herkesin paylaştığı uç.

    argv elle taranmaz: argparse kısaltmayı kabul eder (`--tr http`), elle tarama
    kabul etmez ve sunucu HTTP'de dosya erişimi AÇIK çalışırdı. mcpcore'un kendi
    ayrıştırıcısı kullanılır — taşımayı seçen şeyin aynısı. Ortam değişkeni ayrıca
    ihtiyatla okunur ("Http", "http "): şüphede paylaşılan sayılır.
    """
    if os.environ.get("MCP_TRANSPORT", "").strip().lower() == "http":
        return True
    try:
        from mcpcore import build_parser
        ns, _ = build_parser("tkgm").parse_known_args(sys.argv[1:])
        return ns.transport == "http"
    except SystemExit:  # geçersiz taşıma değeri: ihtiyatla paylaşılan
        return True


def uzak_mi() -> bool:
    """Dosya erişimi kapalı mı. TKGM_DOSYA_ERISIMI=1/0 ile ezilebilir; KVKK kapısı
    (tapu kaydı) bu bayrağa DEĞİL `paylasilan_mi`ye bakar."""
    zorla = os.environ.get("TKGM_DOSYA_ERISIMI")
    if zorla is not None:
        return zorla.strip().lower() not in ("1", "true", "evet")
    return paylasilan_mi()


def dosya_oku(yol: str) -> str:
    if uzak_mi():
        raise ErisimHatasi("Paylaşılan sunucuda dosya yolu okunmaz; dosyanın metnini `icerik` ile verin.")
    yol = os.path.abspath(os.path.expanduser(yol))
    if not yol.lower().endswith(_UZANTILAR):
        raise ErisimHatasi("Yalnız %s uzantılı dosyalar okunur." % ", ".join(_UZANTILAR))
    if not os.path.isfile(yol):
        raise ErisimHatasi("Dosya bulunamadı: %s" % yol)
    if os.path.getsize(yol) > _AZAMI_BOYUT:
        raise ErisimHatasi("Dosya 5 MB sınırını aşıyor.")
    with open(yol, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def cikti_klasoru() -> str:
    return os.path.normpath(os.environ.get("TKGM_CIKTI")
                            or os.path.join(os.path.expanduser("~"), "ArthurLegal", "tkgm"))


def dosya_adi(parsel: Dict[str, Any], ek: str) -> str:
    oz = parsel["oznitelik"]
    parcalar = [str(oz[k]) for k in ("ilce", "mahalle", "ada", "parsel") if oz.get(k)]
    govde = "-".join(parcalar).translate(_TR).lower()
    govde = re.sub(r"[^a-z0-9]+", "-", govde).strip("-")[:80] or parsel["ref"]
    return "%s_%s" % (govde, ek)


def dosya_yaz(ad: str, icerik) -> str:
    """Metin (str) ya da ikili (bytes: docx, xlsx) içeriği çıktı klasörüne yazar."""
    if uzak_mi():
        raise ErisimHatasi("Paylaşılan sunucuda dosya yazılmaz; `inline=true` kullanın.")
    klasor = cikti_klasoru()
    os.makedirs(klasor, exist_ok=True)
    yol = os.path.join(klasor, ad)
    if isinstance(icerik, bytes):
        with open(yol, "wb") as fh:
            fh.write(icerik)
    else:
        with open(yol, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(icerik)
    return yol


_CEVAP_SINIRI = 200_000     # karakter
_CEVAP_SAYISI = 200         # klasörde tutulan en yeni cevap


def _cevap_klasoru() -> str:
    return os.path.join(cikti_klasoru(), "cevaplar")


def cevap_yaz(baslik: str, metin: str, ref: Any = None) -> str:
    """Claude'un cevabını yerel arayüze teslim eder (arayuze_yaz). Arayüz ayrı bir süreçtir;
    ortak nokta çıktı klasörüdür. Yazım atomiktir: arayüz yarım dosya okumaz."""
    if uzak_mi():
        raise ErisimHatasi("arayuze_yaz yalnız yerel kipte çalışır: paylaşılan uç kullanıcının diskine yazamaz.")
    klasor = _cevap_klasoru()
    os.makedirs(klasor, exist_ok=True)
    kayit = {"baslik": str(baslik)[:200], "metin": str(metin)[:_CEVAP_SINIRI],
             "ref": None if ref is None else str(ref)[:64], "zaman": time.time()}
    ad = "%d_%s.json" % (int(kayit["zaman"] * 1000), secrets.token_hex(3))
    gecici = os.path.join(klasor, ad + ".tmp")
    with open(gecici, "w", encoding="utf-8") as fh:
        json.dump(kayit, fh, ensure_ascii=False)
    os.replace(gecici, os.path.join(klasor, ad))
    eskiler = sorted(f for f in os.listdir(klasor) if f.endswith(".json"))[:-_CEVAP_SAYISI]
    for f in eskiler:
        try:
            os.remove(os.path.join(klasor, f))
        except OSError:
            pass
    return os.path.join(klasor, ad)


def cevaplar(sinir: int = 30) -> List[Dict[str, Any]]:
    klasor = _cevap_klasoru()
    if not os.path.isdir(klasor):
        return []
    out = []
    for f in sorted((f for f in os.listdir(klasor) if f.endswith(".json")), reverse=True)[:sinir]:
        try:
            with open(os.path.join(klasor, f), encoding="utf-8") as fh:
                k = json.load(fh)
            out.append({"id": f[:-5], "baslik": str(k.get("baslik", "")), "metin": str(k.get("metin", "")),
                        "ref": k.get("ref"), "zaman": k.get("zaman")})
        except (OSError, ValueError):
            continue
    return out


_IZIN_YOLU = ("Tapu ve Kadastro Verilerinin İşlenmesi ve Elektronik Ortamda Yapılacak İşlemler Hakkında "
              "Yönetmelik (RG 08.06.2022/31860) m. 6/1: veri talebi ve sorgu için Genel Müdürlük ile protokol VEYA elektronik kabul beyanı; toplu paylaşım için protokol (m. 34/c; Veri Paylaşımı Üst Komisyonu, m. 33-34). TKGM 2026 döner sermaye cetveli §8 tekil sorguyu da fiyatlar: günlük 10 sorguya kadar ücretsiz kota (8.1), aylık abonelik (8.2), protokolsüz ücretli parsel geometrisi (8.3.1).")


def canli_durum() -> Dict[str, Any]:
    """Resmî veri paylaşım kanalının (protokol) durumu. Bu kapı ağ çağrısı yapmaz; bugünkü
    canlı sorgu tkgm_canli'dedir."""
    izin = os.environ.get("TKGM_IZIN_BELGESI", "").strip()
    adres = os.environ.get("TKGM_SERVIS_URL", "").strip()
    if not (izin and adres):
        return {"protokol_kanali": "tanımlı değil", "yol": _IZIN_YOLU}
    return {"protokol_kanali": "tanımlı; adaptör TKGM'nin servis sözleşmesine göre yazılacak",
            "izin_belgesi": izin}
