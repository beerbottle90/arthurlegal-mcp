"""Resmî Gazete fihrist triyajı — cihazdan hiç veri çıkarmayan konu ön elemesi.

Standart kütüphane. Ağ yok, anahtar yok, servis yok, ikinci süreç yok.

NE İŞE YARAR
------------
``resmi_gazete_tara`` bugün metinsel arama yapar: sorgudaki her kelime başlıkta
harfi harfine geçmek zorundadır. Bu, bilinen bir terimi aramak için doğrudur
ama bir konuyu TARAMAK için yetersizdir — çünkü Resmî Gazete başlıkları konu
adını çoğu zaman içermez. Etiketli 1414 kalemde ölçüldü: konu adını harfi
harfine aramak, o konunun gerçek kalemlerinin ancak bir kısmını bulur.

    konu      gerçek pozitif    'konu adı' başlıkta geçen
    enerji         179                 103   (%58)
    vergi           87                  53   (%61)
    rekabet         37                   5   (%14)
    icra            52                   5   (%10)

Kaçanlar uydurma değil, sıradan işler: "Şarj Hizmeti Yönetmeliği" ve "Türkiye
Emisyon Ticaret Sistemi Yönetmeliği" enerjidir ama 'enerji' geçmez; "Konkordato
Gider Avansı Tarifesi" icradır ama 'icra' geçmez; "Tahsilat Genel Tebliği"
vergidir ama 'vergi' geçmez. ``icra`` sütunundaki %10, metinsel aramanın bu iş
için neden yetmediğini tek başına anlatır.

Bu modül aynı fihrist kalemlerine konu OLASILIĞI verir, böylece "son 60 günde
enerjiyle ilgili ne çıktı" sorusu — bugün hiç sorulamayan bir soru —
sorulabilir hâle gelir.

NASIL ÇALIŞIR
-------------
İki katman, ``p = max(kural, model)``:

1. **Kural katmanı.** Dar ve yüksek isabetli regex desenleri (EPDK'nın tam adı,
   "malvarlığının dondurulması" gibi). Eşleşirse p = 1.0. Kural yalnız YUKARI
   çeker; modeli asla bastırmaz. Bir kurumun kendi personel/iç idare
   düzenlemeleri istisnadır — "Gelir İdaresi Başkanlığı Personeli Görevde
   Yükselme Yönetmeliği" bir vergi düzenlemesi değildir.
2. **Model katmanı.** Karakter n-gramı (3-5) + tf-idf + lojistik regresyon,
   Platt ile kalibre. Karakter n-gramı bilinçli: Türkçe eklemeli bir dildir ve
   "vergisinin/vergiye/vergilendirme" kelime tabanlı bir modelde üç ayrı şeydir.

Model ``arthurlegal-1.9.1-jev-edition`` deposunda eğitildi; buraya yalnız
çıkarım geldi. Eğitim numpy ister, çıkarım istemez.

SINIRI — ÖNCE BUNU OKUYUN
--------------------------
Bu bir ÖN ELEMEDİR, kapsam garantisi DEĞİLDİR. Kat dışı ölçümde (3581 kalem,
altın küme dışarıda) 0.20 eşiğinde duyarlılık: enerji %97, rekabet %100,
vergi %93, icra %92. Eşik 0.02'ye indirilse bile icra %92'de kalıyor — yani
kaçan kalem eşiği düşürmekle kurtarılmıyor, modelin tavanı budur.

Somut bir kaçak: "Katı Yakıtların Kontrolü Yönetmeliği" bir enerji
düzenlemesidir, ama bu motor ona 0.01 verir ve eler. Sebebi öğretici — katı
yakıtın kömür demek olduğunu bilmek DÜNYA BİLGİSİ ister ve 820 günlük fihrist
gövdesinde bu kalıptan bir tane vardır; model öğrenemez. Bir dil modeli bunu
yakalar (ölçüldü: Jev 0.49). Yani konu süzgeci ucuz ve gizliliklidir, ama
kavrayışlı değildir; kapsam gerektiğinde tam liste okunmalıdır.

Sonuç: **yayım teyidi gibi eksiksizlik gerektiren işlerde konu süzgeci
kullanılmamalıdır.** Bu yüzden süzgeç opsiyoneldir, varsayılan olarak kapalıdır
ve çıktıda kaç kalemin elendiği her zaman açıkça yazılır.

Gövde yalnız Resmî Gazete fihristidir (820 gün) ve konular yalnız dörttür:
enerji, rekabet, vergi, icra. Başka konu sorulursa modül dürüstçe reddeder.
"""

from __future__ import annotations

import json
import math
import re
import struct
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_KOK = Path(__file__).resolve().parent

# DİKKAT — model ``model/`` altındadır, ``data/`` altında DEĞİL. Taşımayın.
#
# ``data/`` yeniden üretilebilir, onlarca megabaytlık ve önyüklemede taranan
# indeksler içindir; ``build_bundle.py`` onu SKIP_DIRS ile toptan atlar ve
# üretilen paketin .gitignore'u da eler. Triyaj modeli ise 715 KB'lık, sürümlü
# ve TARANAMAZ bir yapıttır: taranamaz, çünkü eğitimle üretilir. ``data/``
# altına konursa dağıtıma hiç girmez ve uçtaki her ``konu`` çağrısı "model
# bulunamadı" der — üstelik yalnız o çağrı başarısız olur, sunucu sağlıklı
# görünmeye devam eder. ``status`` bu yüzden triyaj künyesini de raporlar.
_MODEL = _KOK / "model" / "rg_triyaj_saf"

_KATLA = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def katla(metin: str) -> str:
    """Türkçe büyük/küçük ve aksan duyarsız karşılaştırma anahtarı.

    Eğitimdeki ``jev.katla`` ile BİREBİR aynı olmak zorundadır; ayrışırsa model
    kendi sözlüğünü tanımaz ve sessizce sıfır döndürür. Eşdeğerlik testle
    kilitlidir.
    """
    metin = metin.replace("İ", "i").replace("I", "ı")
    metin = unicodedata.normalize("NFC", metin.lower())
    return metin.translate(_KATLA)


class TriyajYok(Exception):
    """Model dosyaları yok ya da bozuk — çağıran bunu kullanıcıya söylemeli."""


class Triyaj:
    """Tembel yüklenen konu sınıflandırıcı. Süreç başına bir kopya yeter."""

    def __init__(self, ust: Dict[str, Any], idf: Tuple[float, ...],
                 agirlik: Dict[str, Tuple[float, ...]]) -> None:
        self._ust = ust
        self._idf = idf
        self._w = agirlik
        self._esleme = {g: i for i, g in enumerate(ust["sozluk"])}
        self._n_min, self._n_max = ust["ngram"]
        self.konular: Tuple[str, ...] = tuple(ust["konular"])
        self.esik: float = float(ust["esik"])
        self._ic_idare = re.compile(ust["ic_idare"])
        self._kurallar: Dict[str, List[Dict[str, Any]]] = {k: [] for k in self.konular}
        for kural in ust["kurallar"]:
            self._kurallar.setdefault(kural["konu"], []).append(
                dict(kural, re=re.compile(kural["desen"])))

    # -- yükleme ----------------------------------------------------------- #

    @classmethod
    def yukle(cls, kok: Optional[Path] = None) -> "Triyaj":
        yol = Path(kok) if kok is not None else _MODEL
        js, bn = yol.with_suffix(".json"), yol.with_suffix(".bin")
        if not js.exists() or not bn.exists():
            raise TriyajYok("Triyaj modeli bulunamadı: %s(.json/.bin)" % yol)
        ust = json.loads(js.read_text(encoding="utf-8"))
        V = int(ust["boyut"])
        ham = bn.read_bytes()
        gerek = 4 * V * (1 + len(ust["konular"]))
        if len(ham) != gerek:
            raise TriyajYok("Triyaj .bin boyu tutmuyor: %d beklenen %d"
                            % (len(ham), gerek))
        duz = struct.unpack("<%df" % (len(ham) // 4), ham)
        idf = duz[:V]
        agirlik = {k: duz[V * (i + 1):V * (i + 2)]
                   for i, k in enumerate(ust["konular"])}
        return cls(ust, idf, agirlik)

    # -- özellik ----------------------------------------------------------- #

    def _vektor(self, metin: str) -> Dict[int, float]:
        """L2 normlu seyrek tf-idf. Eğitimdeki ``Sozluk.vektor`` ile aynı."""
        s = " " + " ".join(katla(metin).split()) + " "
        sayac: Dict[str, int] = {}
        for n in range(self._n_min, self._n_max + 1):
            for i in range(len(s) - n + 1):
                g = s[i:i + n]
                sayac[g] = sayac.get(g, 0) + 1
        seyrek: Dict[int, float] = {}
        for g, c in sayac.items():
            j = self._esleme.get(g)
            if j is not None:
                seyrek[j] = (1.0 + math.log(c)) * self._idf[j]
        norm = math.sqrt(sum(v * v for v in seyrek.values()))
        if norm > 0:
            for j in seyrek:
                seyrek[j] /= norm
        return seyrek

    # -- çıkarım ----------------------------------------------------------- #

    def kural_eslesmeleri(self, metin: str, konu: str) -> List[Dict[str, Any]]:
        katli = katla(metin)
        vurus = []
        for kural in self._kurallar.get(konu, ()):
            if kural["re"].search(katli) is None:
                continue
            if kural["kurum_adi"] and self._ic_idare.search(katli):
                continue
            vurus.append(kural)
        return vurus

    def model_p(self, konu: str, metin: str) -> float:
        sk = self._ust["skaler"].get(konu)
        if sk is None:
            return 0.0
        w = self._w[konu]
        ham = sum(w[j] * v for j, v in self._vektor(metin).items()) + sk["b"]
        z = sk["platt_a"] * ham + sk["platt_c"]
        # Taşmayı önleyen kararlı sigmoid; z büyük negatifte math.exp patlar.
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        e = math.exp(z)
        return e / (1.0 + e)

    def p(self, konu: str, metin: str) -> float:
        """max(kural, model) — kural yalnız yukarı çeker."""
        if konu not in self.konular:
            raise ValueError("Bilinmeyen konu: %s (geçerli: %s)"
                             % (konu, ", ".join(self.konular)))
        if self.kural_eslesmeleri(metin, konu):
            return 1.0
        return self.model_p(konu, metin)

    def neden(self, konu: str, metin: str, kac: int = 6) -> Dict[str, Any]:
        """Kararın dayanağı. Hukukçu bir süzgece körlemesine güvenemez."""
        kurallar = self.kural_eslesmeleri(metin, konu)
        if kurallar:
            return {"karar": 1.0, "dayanak": "kural",
                    "kurallar": [{"desen": k["desen"], "gerekce": k["gerekce"]}
                                 for k in kurallar]}
        w = self._w[konu]
        katki = [(w[j] * v, self._ust["sozluk"][j])
                 for j, v in self._vektor(metin).items()]
        katki.sort(key=lambda t: -abs(t[0]))
        return {"karar": round(self.p(konu, metin), 4), "dayanak": "model",
                "ngramlar": [{"ngram": g, "katki": round(c, 4)}
                             for c, g in katki[:kac]]}


_ONBELLEK: Dict[str, Any] = {}


def motor() -> Triyaj:
    """Süreç ömrü boyunca tek kopya; ilk ``konu`` çağrısında yüklenir."""
    m = _ONBELLEK.get("motor")
    if m is None:
        m = _ONBELLEK["motor"] = Triyaj.yukle()
    return m


def kunye() -> Dict[str, Any]:
    """Durum raporu için: model var mı, neyi kapsıyor, ne kadar kaçırıyor."""
    try:
        m = motor()
    except TriyajYok as exc:
        return {"var": False, "neden": str(exc)}
    return {"var": True, "konular": list(m.konular), "esik": m.esik,
            "sozluk": m._ust["boyut"], "kural": len(m._ust["kurallar"]),
            "kapsam": "Resmî Gazete fihristi (bölüm + başlık)",
            "olcum": m._ust["olcum"]}
