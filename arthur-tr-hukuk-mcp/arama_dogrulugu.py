"""Arama doğruluk testi: cevabı önceden bilinen sorularda doğru kayıt ilk sıralarda mı?

Duman testi (duman_testi.py) "kaynak cevap veriyor mu" sorusuna bakar; bu test "doğru kaydı getiriyor mu"
sorusuna. 0.8.0'a kadar içtihat araması kaynağa tarihe göre sıralatıyordu: duman testi her gün yeşildi,
konuyu çözen kararlar ise ilk sayfaya hiç gelmiyordu (mcp #70). Kalemler arama_kalemleri.json'da; her biri bir araç
çağrısı ve ilk_n sonuçta görünmesi gereken kayıtlar. Her kalemin sonucu:

  GECTI    beklenen kayıtlardan en az en_az kadarı ilk_n sonuçta
  KALDI    kaynak cevap verdi ama beklenen kayıtlar ilk_n sonuçta yok (boş liste dahil)
  ERISIM   kaynak o an cevap vermedi (zaman aşımı, bağlantı, 5xx, unavailable): sıralama ölçülemedi
  HATA     diğer her şey (araç yok, yanıt ayrıştırılamadı)

Yalnız standart kütüphane; istemci duman_testi.Istemci. Bedesten hız sınırı (10 istek / 30 sn) için
çağrılar sırayla ve arada bekleyerek yapılır. Dil modeli kullanılmaz.

  python arama_dogrulugu.py --url http://127.0.0.1:8000/mcp            # arka uç tek başına (CI)
  python arama_dogrulugu.py ... --json sonuc.json --kalem ictihat-haciz-takas

Çıkış kodu: hepsi GECTI ise 0; yalnız ERISIM varsa 2; KALDI ya da HATA varsa 1.
Günlük koşu: .github/workflows/arama-dogrulugu.yml. Dağıtım kapısı: .github/workflows/fly-dagitim.yml.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
from typing import Any, Dict, List, Optional

from duman_testi import Istemci, _sinif

KALEMLER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arama_kalemleri.json")
BEKLEME_SN = 3.5  # Bedesten: 10 istek / 30 sn, kova mevzuatla ortak


def _norm(deger: Any) -> str:
    metin = str(deger if deger is not None else "").replace("İ", "i").replace("I", "ı").lower()
    return " ".join(metin.split())


def _liste(sonuc: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    for anahtar in ("results", "items"):
        liste = sonuc.get(anahtar)
        if isinstance(liste, list):
            return [s for s in liste if isinstance(s, dict)]
    return None


def eslesir(satir: Dict[str, Any], beklenen: Dict[str, Any]) -> bool:
    return all(_norm(satir.get(alan)) == _norm(deger) for alan, deger in beklenen.items())


def degerlendir(kalem: Dict[str, Any], sonuc: Dict[str, Any]) -> Dict[str, Any]:
    """Bir araç yanıtını kalemin beklentisiyle karşılaştırır (ağsız; testler bunu çağırır)."""
    satir: Dict[str, Any] = {"id": kalem["id"]}
    if sonuc.get("error") or sonuc.get("unavailable"):
        hata = str(sonuc.get("error") or sonuc.get("unavailable"))
        sinif = _sinif(hata)
        if sonuc.get("unavailable") or sonuc.get("error_code") in ("unavailable", "upstream_blocked"):
            sinif = "ERISIM"
        satir.update(sinif=sinif, ayrinti=hata[:300])
        return satir
    liste = _liste(sonuc)
    if liste is None:
        satir.update(sinif="HATA", ayrinti="yanıtta results/items listesi yok")
        return satir
    ilk_n = int(kalem.get("ilk_n", 10))
    en_az = int(kalem.get("en_az", 1))
    siralar = []
    for beklenen in kalem["beklenen"]:
        sira = next((i + 1 for i, s in enumerate(liste[:ilk_n]) if eslesir(s, beklenen)), None)
        siralar.append({"beklenen": beklenen, "sira": sira})
    bulunan = sum(1 for s in siralar if s["sira"])
    satir.update(sinif="GECTI" if bulunan >= en_az else "KALDI", bulunan=bulunan, en_az=en_az, ilk_n=ilk_n,
                 siralar=siralar, donen=len(liste))
    if satir["sinif"] == "KALDI":
        satir["ayrinti"] = "%d/%d beklenen kayıt ilk %d sonuçta (en az %d gerekiyordu)" % (
            bulunan, len(siralar), ilk_n, en_az)
    return satir


def kalem_calistir(istemci: Istemci, kalem: Dict[str, Any]) -> Dict[str, Any]:
    bas = time.monotonic()
    try:
        satir = degerlendir(kalem, istemci.cagir(kalem["arac"], dict(kalem["argumanlar"])))
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        satir = {"id": kalem["id"], "sinif": "ERISIM", "ayrinti": str(exc)[:300]}
    except (KeyError, TypeError, ValueError) as exc:
        satir = {"id": kalem["id"], "sinif": "HATA", "ayrinti": "%s: %s" % (type(exc).__name__, exc)}
    satir["sure_sn"] = round(time.monotonic() - bas, 1)
    return satir


def cikis_kodu(satirlar: List[Dict[str, Any]]) -> int:
    siniflar = {s["sinif"] for s in satirlar}
    if siniflar & {"KALDI", "HATA"}:
        return 1
    return 2 if "ERISIM" in siniflar else 0


def issue_govdesi(satir: Dict[str, Any], kalem: Dict[str, Any], kosu: str = "") -> str:
    """Düşen kalem için Türkçe issue metni (günlük iş kullanır)."""
    satirlar = ["Arama doğruluk testinde `%s` kalemi **%s**." % (satir["id"], satir["sinif"]), "",
                "- Araç: `%s` %s" % (kalem["arac"], json.dumps(kalem["argumanlar"], ensure_ascii=False)),
                "- Neden bu kalem var: %s" % kalem.get("kaynak", ""),
                "- Son ölçüm: %s" % kalem.get("olcum", "")]
    if satir.get("ayrinti"):
        satirlar.append("- Bugün: %s" % satir["ayrinti"])
    for s in satir.get("siralar", []):
        satirlar.append("  - %s → %s" % (json.dumps(s["beklenen"], ensure_ascii=False),
                                         "sıra %d" % s["sira"] if s["sira"] else "ilk %d sonuçta yok" % satir["ilk_n"]))
    satirlar += ["", "Ne yapılır: kaynağın sıralaması ya da yanıt biçimi değişmiş olabilir; sorguyu elle çalıştırıp "
                 "beklenen kaydın nerede olduğuna bakın. Kod düzeltilir ya da kalem gerekçeyle güncellenir. Bu düşüş "
                 "sürerken Fly dağıtımı durur; bilerek dağıtmak için Run workflow > arama_kabul alanına gerekçe."]
    if kosu:
        satirlar += ["", "Koşu: %s" % kosu]
    return "\n".join(satirlar)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Arama doğruluk testi")
    p.add_argument("--url", default="http://127.0.0.1:8000/mcp", help="MCP ucu (…/mcp)")
    p.add_argument("--onek", default="", help="araç adı öneki; ağ geçidinde tr_")
    p.add_argument("--kalemler", default=KALEMLER)
    p.add_argument("--kalem", action="append", help="yalnız bu kalem(ler)")
    p.add_argument("--json", help="sonucu bu dosyaya yaz")
    p.add_argument("--bekleme", type=float, default=BEKLEME_SN)
    p.add_argument("--issue-govdesi", metavar="ID", help="--json dosyasından bu kalemin issue metnini yaz ve çık")
    p.add_argument("--kosu", default="", help="issue metnine eklenecek koşu bağlantısı")
    a = p.parse_args(argv)
    with open(a.kalemler, encoding="utf-8") as f:
        kalemler = {k["id"]: k for k in json.load(f)["kalemler"]}
    if a.issue_govdesi:
        with open(a.json, encoding="utf-8") as f:
            satir = next(s for s in json.load(f)["sonuclar"] if s["id"] == a.issue_govdesi)
        print(issue_govdesi(satir, kalemler[a.issue_govdesi], a.kosu))
        return 0
    istemci = Istemci(a.url, a.onek)
    satirlar: List[Dict[str, Any]] = []
    for kalem in kalemler.values():
        if a.kalem and kalem["id"] not in a.kalem:
            continue
        if satirlar and a.bekleme:
            time.sleep(a.bekleme)
        s = kalem_calistir(istemci, kalem)
        satirlar.append(s)
        siralar = ",".join(str(x["sira"] or "-") for x in s.get("siralar", []))
        print("%-22s %-6s %5.1fs  sıra[%s] %s" % (s["id"], s["sinif"], s["sure_sn"], siralar, s.get("ayrinti", "")),
              flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"url": a.url, "zaman": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "sonuclar": satirlar}, f, ensure_ascii=False, indent=1)
    return cikis_kodu(satirlar)


if __name__ == "__main__":
    sys.exit(main())
