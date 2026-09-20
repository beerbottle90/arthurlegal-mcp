"""tkgm_kaynak — verinin nereden geldiği ve dosyanın nereye gittiği.

İki ayrı sınır burada çizilir:

1. **TKGM sınırı.** Parsel Sorgu Kullanım Koşulları md. 3, uygulamanın web
   servislerine TKGM izni olmadan doğrudan/dolaylı erişimi yasaklar. Bu yüzden
   canlı kaynak bir *kapıdır*, gövde değil: izin belgesi ve TKGM'nin verdiği
   servis adresi tanımlanmadıkça hiçbir ağ çağrısı yapılmaz. Belgelenmemiş
   Parsel Sorgu uç adresleri bu depoya bilerek yazılmadı — herkese açık bir
   depoda duran hazır bir istemci, md. 3'ün yasakladığı şeyin ta kendisidir.

2. **Dosya sistemi sınırı.** Birleşik uç (Fly) kimlik doğrulamasızdır. Orada
   yol alan bir araç, sunucunun diskini internete açar. Dosya okuma/yazma
   yalnız stdio taşımasında (kullanıcının kendi makinesi) açıktır.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict

_UZANTILAR = (".geojson", ".json", ".kml")
_AZAMI_BOYUT = 5_000_000
_TR = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iisSgGuUoOcC")


class ErisimHatasi(Exception):
    """İstenen dosya işlemi bu çalışma kipinde yapılamaz."""


def uzak_mi() -> bool:
    """HTTP taşıması = paylaşılan sunucu. TKGM_DOSYA_ERISIMI=1/0 ile ezilebilir."""
    zorla = os.environ.get("TKGM_DOSYA_ERISIMI")
    if zorla is not None:
        return zorla.strip().lower() not in ("1", "true", "evet")
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--transport=http" or (a == "--transport" and argv[i + 1:i + 2] == ["http"]):
            return True
    return os.environ.get("MCP_TRANSPORT", "").lower() == "http"


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


def canli_durum() -> Dict[str, Any]:
    """Canlı TKGM kaynağının durumu. Bu sürümde hiçbir koşulda ağ çağrısı yapılmaz."""
    izin = os.environ.get("TKGM_IZIN_BELGESI", "").strip()
    adres = os.environ.get("TKGM_SERVIS_URL", "").strip()
    if not (izin and adres):
        return {"acik": False,
                "neden": "TKGM izni/protokolü tanımlı değil (Kullanım Koşulları md. 3). "
                         "Yol: Tapu ve Kadastro Verilerinin İşlenmesi ve Elektronik Ortamda "
                         "Yapılacak İşlemler Hakkında Yönetmelik (RG 08.06.2022/31860) m. 6 ve 10 "
                         "uyarınca Genel Müdürlük ile protokol; talebi Veri Paylaşımı Üst "
                         "Komisyonu karara bağlar (m. 33-34)."}
    return {"acik": False, "izin_belgesi": izin,
            "neden": "İzin tanımlı; ancak canlı adaptör, TKGM'nin protokolle vereceği servis "
                     "sözleşmesine göre yazılacak — henüz uygulanmadı."}
