#!/usr/bin/env python3
"""tkgm-mcp — tapu-kadastro parsel araçları. Kimlik doğrulama yok, yalnız stdlib.

    python server.py                                 # stdio (tam özellik: dosya okur/yazar)
    python server.py --transport http --port 8060    # paylaşılan kip: dosya erişimi kapalı

Bu sunucu TKGM'ye BAĞLANMAZ. Parsel Sorgu Kullanım Koşulları md. 3 uygulamanın
web servislerine izinsiz doğrudan/dolaylı erişimi, md. 4 ticari kullanımı
yasaklar. Veri yolu kullanıcıdan geçer: kullanıcı parseli kendi tarayıcısında
sorgular, arayüzden dosyayı indirir, buraya verir. Buranın kattığı şey,
o dosyadan sonrasıdır: ölçü, ölçekli kroki, biçim dönüşümü ve parselden
hukuka köprü.

Token disiplini: görsel ve dışa aktarım dosyaya yazılır, modele yol + birkaç
satır özet döner. Yanıtlar sıkıştırılmış JSON'dur. Parseller içerik özetinden
türeyen bir `ref` ile anılır; geometri her çağrıda yeniden gönderilmez.
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List

import tkgm_geo as geo
import tkgm_harc as harc
import tkgm_harita as harita
import tkgm_hukuk as hukuk
import tkgm_kaynak as kaynak
import tkgm_koridor as koridor
import tkgm_parsel as parseller
import tkgm_rapor as rapor
import tkgm_tapu as tapu
from mcpcore import McpError, Tool, run
from tkgm_aktar import BICIMLER, aktar
from tkgm_analiz import olc, tr_bicim
from tkgm_kroki import ciz

__version__ = "0.2.0"

PARSEL_SORGU = "https://parselsorgu.tkgm.gov.tr/"
BAGLANTILAR = {
    "parsel_sorgu": PARSEL_SORGU,
    "web_tapu": "https://webtapu.tkgm.gov.tr/",
    "e_devlet_tapu_bilgileri": "https://www.turkiye.gov.tr/tapu-bilgileri-sorgulama",
    "tkgm_randevu": "https://randevu.tkgm.gov.tr/",
}

INSTRUCTIONS = """Tapu-kadastro parsel araçları: ölçü, ölçekli kroki, biçim dönüşümü, hukuk köprüsü.

TKGM'YE BAĞLANMAZ. Parsel Sorgu Kullanım Koşulları md. 3 web servislerine izinsiz
doğrudan/dolaylı erişimi, md. 4 ticari kullanımı yasaklar. Parsel verisini KULLANICI
getirir: `baglanti` ile Parsel Sorgu'yu açar, parseli sorgular, arayüzden GeoJSON/KML
indirir, `parsel_oku`ya verir. Ada/parsel numarasından geometri UYDURMAYIN; dosya yoksa
dosyayı isteyin.

AKIŞ. `parsel_oku` → `ref` döner → `geometri` / `kroki` / `disa_aktar` / `hukuk_koprusu`
o `ref` ile çağrılır. Komşu parselleri de okutup `kroki`ye `komsular` olarak verin.

VERİNİN DEĞERİ. Parsel Sorgu verisi bilgi amaçlıdır, resmî işlemde kullanılamaz; malik,
şerh, beyan, rehin İÇERMEZ. Bunlar için kullanıcı kendi e-Devlet/Web Tapu oturumundan
belge alır. Kroki, aplikasyon krokisi veya röperli kroki yerine geçmez. Hesap alanı ile
dosyadaki alan farkı bir İŞARETTİR, hata tespiti değildir.

HUKUK. `hukuk_koprusu` hüküm metni vermez, adres verir: dayanak madde numaraları ve
çalıştırılacak `tr_mevzuat_*` / `tr_ictihat_*` çağrıları. Metni o araçlarla çekmeden
alıntılamayın; süreleri teyitsiz yazmayın.

TOKEN. Görseller dosyaya yazılır, yol döner. `inline=true` yalnız dosya yazılamayan
(paylaşılan) sunucuda ya da kullanıcı SVG metnini açıkça istediğinde."""

REHBER = """TKGM araçları — ne yapar, ne yapmaz
1) VERİ YOLU: bu sunucu TKGM servislerini çağırmaz (Kullanım Koşulları md. 3). Kullanıcı
   Parsel Sorgu'da parseli bulur, haritada parsele tıklar, bilgi kutusundaki ⋮ menüsünden GeoJSON/KML indirir.
   `baglanti` koordinattan doğrudan bağlantı, idari sorgu için adım listesi üretir.
2) OKU: parsel_oku(dosya=… | icerik=…) → ref, öznitelik, hesap alanı, nitelik rejimi uyarıları.
3) ÖLÇ/ÇİZ: geometri(ref) kenar-semt-açı; kroki(ref, komsular=[…]) A4 ölçekli SVG;
   disa_aktar(ref, bicim=geojson|kml|dxf|csv); koordinat_donustur ITRF96 TM3°/UTM ↔ coğrafi.
4) HUKUK: hukuk_koprusu(konu=… | ref=…) → dayanak adresleri + hazır tr_ araç çağrıları.
5) KAPSAM DIŞI (şimdilik): canlı parsel sorgusu ve toplu/güzergâh dökümü TKGM veri paylaşım
   protokolü ister (Tapu ve Kadastro Verilerinin İşlenmesi ve Elektronik Ortamda Yapılacak İşlemler
   Hakkında Yönetmelik, RG 08.06.2022/31860, m. 6 ve 10 — tr_mevzuat_ara ile teyit edin);
   malik-şerh-rehin bilgisi yalnız kullanıcının kendi e-Devlet/Web Tapu oturumundan alınır.
Ticari/toplu kullanım için md. 4'e dikkat: Parsel Sorgu çıktısı ticari amaçla kullanılamaz."""


def _kisa(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)


def _parsel(args: Dict[str, Any], anahtar: str = "ref") -> Dict[str, Any]:
    ref = str(args.get(anahtar) or "").strip()
    if not ref:
        raise McpError("`%s` gerekli: önce parsel_oku çağırın." % anahtar)
    p = parseller.getir(ref)
    if p is None:
        raise McpError("ref '%s' bellekte yok (sunucu yeniden başlamış olabilir). Dosyayı "
                       "parsel_oku ile yeniden okutun." % ref)
    return p


def _cikti(parsel: Dict[str, Any], ek: str, metin: str, inline: bool, alan: str) -> Dict[str, Any]:
    """Dosyaya yaz ve yolu döndür; paylaşılan sunucuda ya da istenirse metni döndür."""
    if inline or kaynak.uzak_mi():
        return {alan: metin}
    yol = kaynak.dosya_yaz(kaynak.dosya_adi(parsel, ek), metin)
    return {"dosya": yol, "boyut_kb": round(len(metin.encode("utf-8")) / 1024.0, 1)}


# --------------------------------------------------------------------------- #
def _t_rehber(args: Dict[str, Any]) -> Any:
    return REHBER


def _t_baglanti(args: Dict[str, Any]) -> Any:
    enlem, boylam = args.get("enlem"), args.get("boylam")
    if args.get("ref"):
        olcu = olc(_parsel(args))
        # Köşe ortalaması L/U biçimli parselde dışarı düşer ve bağlantı komşuyu açar;
        # sınıra en uzak İÇ nokta kullanılır.
        (ic_y, ic_x), _ = geo.etiket_noktasi(olcu["tm"][0])
        enlem, boylam = geo.tm_geri(ic_y, ic_x, olcu["dom"])
    out: Dict[str, Any] = {"diger": {k: v for k, v in BAGLANTILAR.items() if k != "parsel_sorgu"}}
    if enlem is not None and boylam is not None:
        enlem, boylam = float(enlem), float(boylam)
        if not (35.0 <= enlem <= 43.0 and 25.0 <= boylam <= 45.5):
            raise McpError("Koordinat Türkiye sınırları dışında; enlem/boylam sırasını kontrol edin.")
        out["parsel_sorgu"] = "%s#ara/cografi/%.6f/%.6f" % (PARSEL_SORGU, enlem, boylam)
    else:
        out["parsel_sorgu"] = PARSEL_SORGU
        yer = [str(args[k]) for k in ("il", "ilce", "mahalle") if args.get(k)]
        out["adimlar"] = [
            "Bağlantıyı açın, kullanım koşullarını onaylayın.",
            "İdari sorguda seçin: %s" % (" > ".join(yer) if yer else "İl > İlçe > Mahalle"),
            "Ada: %s, Parsel: %s girip sorgulayın." % (args.get("ada") or "…", args.get("parsel") or "…"),
            "Haritada parsele tıklayın; açılan bilgi kutusunun köşesindeki ⋮ (üç nokta) menüsünden "
            "GeoJSON (ya da KML) olarak indirin. Komşu parseller için aynısını tek tek yapın.",
            "İndirilen dosyanın yolunu (ya da metnini) parsel_oku aracına verin.",
        ]
    out["not"] = "Bağlantıyı kullanıcı kendi tarayıcısında açar; bu sunucu TKGM'ye istek göndermez."
    return _kisa(out)


def _t_parsel_oku(args: Dict[str, Any]) -> Any:
    dosya, icerik = args.get("dosya"), args.get("icerik")
    if bool(dosya) == bool(icerik):
        raise McpError("`dosya` (yerel yol) ya da `icerik` (GeoJSON/KML metni) — yalnız biri.")
    sistem = args.get("sistem") or "tm3"
    try:
        metin = kaynak.dosya_oku(dosya) if dosya else str(icerik)
        okunan = parseller.oku(metin, ad=str(dosya or "icerik"), dom=args.get("dom"),
                               k0=geo.SISTEMLER[sistem]["k0"])
    except (kaynak.ErisimHatasi, parseller.ParselHatasi) as exc:
        raise McpError(str(exc)) from exc

    liste: List[Dict[str, Any]] = []
    for p in okunan:
        parseller.sakla(p)
        olcu = olc(p)
        kayit: Dict[str, Any] = {"ref": p["ref"], "parsel": parseller.etiket(p),
                                 "alan_m2": olcu["alan_m2"], "kose": olcu["kose_sayisi"]}
        oz = p["oznitelik"]
        for k in ("nitelik", "mevkii", "pafta", "zemin_tipi", "durum", "gittigi_parseller"):
            if oz.get(k):
                kayit[k] = oz[k]
        if "fark_yuzde" in olcu:
            kayit["dosyadaki_alan_m2"] = olcu["tapu_alani_m2"]
            kayit["fark_yuzde"] = olcu["fark_yuzde"]
        for k in ("alan_belirsizligi_m2", "fark_yorumu", "hassasiyet_notu"):
            if k in olcu:
                kayit[k] = olcu[k]
        rejim = hukuk.nitelik_rejimleri(oz.get("nitelik", ""))
        if rejim:
            kayit["rejim"] = [r["uyari"] for r in rejim]
        liste.append(kayit)
    out: Dict[str, Any] = {"okunan": len(liste), "parseller": liste[:40]}
    if len(liste) > 40:
        out["not"] = "İlk 40 parsel listelendi; tamamı bellekte."
    return _kisa(out)


def _t_geometri(args: Dict[str, Any]) -> Any:
    p = _parsel(args)
    olcu = olc(p)
    tm = olcu.pop("tm")
    out: Dict[str, Any] = {"ref": p["ref"], "parsel": parseller.etiket(p)}
    out.update(olcu)
    if args.get("ayrinti", True):
        satirlar: List[str] = []
        acilar: List[str] = []
        kayma = 0
        for c in tm:
            for k in geo.kenarlar(c[0]):
                satirlar.append("%d-%d %s m %sg" % (k["bas"] + kayma, k["son"] + kayma,
                                                    tr_bicim(k["boy"]), tr_bicim(k["semt_g"], 2)))
            acilar += ["%d:%s" % (i + 1 + kayma, tr_bicim(a, 2))
                       for i, a in enumerate(geo.ic_acilar(c[0]))]
            kayma += len(c[0])
        out["kenarlar"] = satirlar[:60]
        out["ic_acilar_g"] = " ".join(acilar[:60])
        if len(satirlar) > 60:
            out["not"] = "İlk 60 kenar; tamamı için disa_aktar bicim=csv."
        out["aciklama"] = "kenar: bas-son boy semt(grad, grid kuzeyinden saat yönünde); açılar grad"
    return _kisa(out)


def _t_kroki(args: Dict[str, Any]) -> Any:
    p = _parsel(args)
    komsular = [_parsel({"ref": r}) for r in (args.get("komsular") or []) if r != p["ref"]]
    svg, ozet = ciz(p, komsular, bool(args.get("koordinat_tablosu")),
                    datetime.date.today().isoformat())
    try:
        ozet.update(_cikti(p, "kroki.svg", svg, bool(args.get("inline")), "svg"))
    except kaynak.ErisimHatasi as exc:
        raise McpError(str(exc)) from exc
    return _kisa(ozet)


def _t_disa_aktar(args: Dict[str, Any]) -> Any:
    p = _parsel(args)
    bicim = str(args.get("bicim") or "geojson").lower()
    if bicim not in BICIMLER:
        raise McpError("bicim: %s" % " | ".join(BICIMLER))
    out: Dict[str, Any] = {"parsel": parseller.etiket(p), "bicim": bicim}
    try:
        out.update(_cikti(p, "parsel.%s" % bicim, aktar(p, bicim), bool(args.get("inline")), "icerik"))
    except kaynak.ErisimHatasi as exc:
        raise McpError(str(exc)) from exc
    return _kisa(out)


def _t_koordinat(args: Dict[str, Any]) -> Any:
    yon = args.get("yon")
    sistem = args.get("sistem") or "tm3"
    k0 = geo.SISTEMLER[sistem]["k0"]
    noktalar = args.get("noktalar") or []
    if not noktalar or len(noktalar) > 500:
        raise McpError("`noktalar`: 1-500 çift.")
    dom = args.get("dom")
    sonuc = []
    if yon == "cografi_tm":
        if dom is None:
            dom = geo.dom_sec(sum(float(n[1]) for n in noktalar) / len(noktalar), sistem)
        for enlem, boylam in noktalar:
            y, x = geo.tm_ileri(float(enlem), float(boylam), dom, k0)
            sonuc.append([round(y, 3), round(x, 3)])
        sira = "girdi [enlem, boylam] → çıktı [Y sağa, X yukarı] m"
    elif yon == "tm_cografi":
        if dom is None:
            raise McpError("tm_cografi için `dom` zorunlu: koordinat hangi dilimde üretildi?")
        for y, x in noktalar:
            enlem, boylam = geo.tm_geri(float(y), float(x), dom, k0)
            sonuc.append([round(enlem, 8), round(boylam, 8)])
        sira = "girdi [Y sağa, X yukarı] m → çıktı [enlem, boylam]"
    else:
        raise McpError("yon: cografi_tm | tm_cografi")
    return _kisa({"sistem": "%s DOM %s" % (geo.SISTEMLER[sistem]["ad"], dom), "sira": sira,
                  "noktalar": sonuc,
                  "not": "ITRF96 ile WGS84 bu amaçla özdeş alınır; ED50 (eski paftalar) DEĞİLDİR, "
                         "ED50 koordinatı datum dönüşümü ister."})


def _t_hukuk(args: Dict[str, Any]) -> Any:
    konu = args.get("konu")
    out: Dict[str, Any] = {}
    nitelik = args.get("nitelik") or ""
    if args.get("ref"):
        p = _parsel(args)
        out["parsel"] = parseller.etiket(p)
        nitelik = nitelik or p["oznitelik"].get("nitelik", "")
    if nitelik:
        out["nitelik"] = nitelik
        out["rejim"] = hukuk.nitelik_rejimleri(nitelik)
    if konu:
        if konu not in hukuk.KONULAR:
            raise McpError("Bilinmeyen konu. Geçerli: %s" % ", ".join(hukuk.KONULAR))
        out["konu"] = hukuk.kopru(konu)
    else:
        out["konular"] = hukuk.konu_listesi()
    out["dogrulama"] = hukuk.DOGRULAMA
    return _kisa(out)


def _t_koridor(args: Dict[str, Any]) -> Any:
    refs = args.get("refs") or []
    if not refs:
        raise McpError("`refs`: koridorla karşılaştırılacak parsellerin ref listesi.")
    secilen = [_parsel({"ref": r}) for r in refs]
    verilen = [k for k in ("hat", "hat_icerik", "hat_dosya") if args.get(k)]
    if len(verilen) != 1:
        raise McpError("Hat için `hat` ([[enlem, boylam]…]), `hat_icerik` ya da `hat_dosya` — yalnız biri.")
    try:
        if args.get("hat"):
            hatlar = [[(float(n[1]), float(n[0])) for n in args["hat"]]]
            if len(hatlar[0]) < 2:
                raise koridor.KoridorHatasi("`hat` en az iki nokta ister.")
        else:
            metin = (kaynak.dosya_oku(args["hat_dosya"]) if args.get("hat_dosya")
                     else str(args["hat_icerik"]))
            hatlar = koridor.hat_oku(metin)
        sonuc = koridor.kesisim(secilen, hatlar, float(args.get("genislik_m") or 0))
    except (koridor.KoridorHatasi, kaynak.ErisimHatasi) as exc:
        raise McpError(str(exc)) from exc

    satirlar = sonuc.pop("satirlar")
    sonuc["satirlar"] = ["%s | %s m² kesişim (%%%s) | eksen %s m%s" % (
        s["parsel"], tr_bicim(s["kesisim_m2"]), tr_bicim(s["oran_yuzde"]), tr_bicim(s["eksen_m"]),
        "" if s["eksen_geciyor"] else " (yalnız tampon)") for s in satirlar[:40]]
    if len(satirlar) > 40:
        sonuc["not"] = "İlk 40 satır (kesişime göre azalan); tamamı CSV'de."
    sonuc["yontem"] = ("Yarı-analitik tarama; sayisal_hata_yuzde_en_kotu aynı taramayla hesaplanan "
                       "parsel alanının Gauss alanından sapmasıdır. Her parsel kendi TM 3° diliminde.")
    if satirlar and not kaynak.uzak_mi():
        sonuc["dosya"] = kaynak.dosya_yaz("koridor_%dm_%dparsel.csv" % (round(sonuc["genislik_m"]),
                                                                       len(satirlar)),
                                          koridor.csv_yaz({"satirlar": satirlar}))
    return _kisa(sonuc)


def _komsular(args: Dict[str, Any], p: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [_parsel({"ref": r}) for r in (args.get("komsular") or []) if r != p["ref"]]


def _t_harita(args: Dict[str, Any]) -> Any:
    p = _parsel(args)
    html = harita.harita_html(p, _komsular(args, p))
    out: Dict[str, Any] = {"parsel": parseller.etiket(p), "altlik": "OpenStreetMap (tarayıcı yükler)"}
    try:
        out.update(_cikti(p, "harita.html", html, bool(args.get("inline")), "html"))
    except kaynak.ErisimHatasi as exc:
        raise McpError(str(exc)) from exc
    return _kisa(out)


def _t_arazi_3d(args: Dict[str, Any]) -> Any:
    # Yükseklik verisi sunucunun Copernicus'tan MB'larca indirmesini gerektirir. Kimlik
    # doğrulamasız paylaşılan uçta bu, isteyen herkesin sunucuya bant genişliği harcatması olur.
    if kaynak.uzak_mi():
        raise McpError("arazi_3d yalnız yerel (stdio) kipte çalışır: DEM indirmesi paylaşılan uçta açılmadı.")
    p = _parsel(args)
    kom = _komsular(args, p)
    try:
        izgara = harita.dem_izgara(*harita.parsel_kutusu(p, kom))
        ozet = harita.arazi_ozeti(p, izgara)
        html = harita.arazi_3d_html(p, kom, izgara, ozet)
        out: Dict[str, Any] = {"parsel": parseller.etiket(p), "arazi": ozet, "kaynak": izgara.get("kaynak"),
                               "indirilen_kb": round(izgara.get("indirilen_bayt", 0) / 1024.0)}
        if izgara.get("uyari"):
            out["uyari"] = izgara["uyari"]
        out.update(_cikti(p, "arazi3d.html", html, bool(args.get("inline")), "html"))
    except harita.HaritaHatasi as exc:
        raise McpError(str(exc)) from exc
    except kaynak.ErisimHatasi as exc:
        raise McpError(str(exc)) from exc
    return _kisa(out)


def _t_foy(args: Dict[str, Any]) -> Any:
    if kaynak.uzak_mi():
        raise McpError("parsel_foyu dosya üretir; yalnız yerel (stdio) kipte çalışır.")
    p = _parsel(args)
    konu = args.get("konu")
    if konu and konu not in hukuk.KONULAR:
        raise McpError("Bilinmeyen konu. Geçerli: %s" % ", ".join(hukuk.KONULAR))
    veri = rapor.foy_docx(p, _komsular(args, p), konu, datetime.date.today().isoformat())
    yol = kaynak.dosya_yaz(kaynak.dosya_adi(p, "foy.docx"), veri)
    return _kisa({"parsel": parseller.etiket(p), "dosya": yol, "boyut_kb": round(len(veri) / 1024.0, 1)})


def _t_portfoy(args: Dict[str, Any]) -> Any:
    if kaynak.uzak_mi():
        raise McpError("portfoy_tablosu dosya üretir; yalnız yerel (stdio) kipte çalışır.")
    refs = args.get("refs") or []
    if not refs:
        raise McpError("`refs`: tabloya girecek parsellerin ref listesi.")
    secilen = [_parsel({"ref": r}) for r in refs]
    veri = rapor.portfoy_xlsx(secilen, datetime.date.today().isoformat())
    yol = kaynak.dosya_yaz("portfoy_%dparsel_%s.xlsx" % (len(secilen), datetime.date.today().isoformat()), veri)
    return _kisa({"parsel_sayisi": len(secilen), "dosya": yol, "boyut_kb": round(len(veri) / 1024.0, 1)})


def _t_tarife_ara(args: Dict[str, Any]) -> Any:
    sorgu = str(args.get("sorgu") or "").strip()
    if len(sorgu) < 2:
        raise McpError("`sorgu`: işlem adı, örn. 'satış', 'ipotek', 'aplikasyon', 'kat irtifakı'.")
    return _kisa({"veri": harc.durum(), "sonuclar": harc.ara(sorgu),
                  "not": "kod'u harc_hesapla'ya verin. isaret: YK = yöresel katsayıyla çarpılır, M = maktu."})


def _t_harc(args: Dict[str, Any]) -> Any:
    th, ds = args.get("tapu_harci_islem"), args.get("doner_sermaye_kod")
    if not th and not ds:
        raise McpError("`tapu_harci_islem` ve/veya `doner_sermaye_kod` verin; kodlar için tarife_ara.")
    out: Dict[str, Any] = {"veri": harc.durum()}
    try:
        if th:
            out["tapu_harci"] = harc.tapu_harci(str(th), float(args.get("bedel") or 0),
                                                args.get("emlak_vergi_degeri"))
        if ds:
            out["doner_sermaye"] = harc.doner_sermaye(str(ds), args.get("yoresel_katsayi"),
                                                      int(args.get("adet") or 1))
    except harc.HarcHatasi as exc:
        raise McpError(str(exc)) from exc
    parcalar = [out[k].get("toplam_tl") for k in ("tapu_harci", "doner_sermaye") if k in out]
    if parcalar and all(p is not None for p in parcalar):
        out["genel_toplam_tl"] = round(sum(parcalar), 2)
    out["uyari"] = harc.ON_HESAP
    return _kisa(out)


def _t_tapu(args: Dict[str, Any]) -> Any:
    if kaynak.uzak_mi():
        raise McpError("Tapu kaydı kişisel veri içerir; bu araç yalnız yerel (stdio) kipte çalışır. "
                       "Kimlik doğrulamasız paylaşılan uca malik bilgisi gönderilmez.")
    metin = str(args.get("metin") or "")
    if len(metin.strip()) < 20:
        raise McpError("`metin`: kullanıcının kendi e-Devlet/Web Tapu oturumundan aldığı belgenin metni.")
    kayit = tapu.ayristir(metin, maskele=args.get("tc_maskele", True) is not False)
    if args.get("ref"):
        p = _parsel(args)
        kayit["capraz_kontrol"] = tapu.capraz_kontrol(kayit, p, olc(p)["alan_m2"])
    return _kisa(kayit)


def _t_status(args: Dict[str, Any]) -> Any:
    return {
        "server": "tkgm-mcp",
        "version": __version__,
        "upstream": "TKGM: YOK (Parsel Sorgu Kullanım Koşulları md. 3). Tek dış çağrı: arazi_3d için "
                    "Copernicus DEM (AWS açık veri), yalnız yerel kipte. OSM karolarını kullanıcının tarayıcısı yükler.",
        "kip": "paylaşılan (dosya erişimi kapalı)" if kaynak.uzak_mi() else "yerel (dosya okur/yazar)",
        "cikti_klasoru": None if kaynak.uzak_mi() else kaynak.cikti_klasoru(),
        "bellekteki_parsel": parseller.bellek_sayisi(),
        "canli_kaynak": kaynak.canli_durum(),
        "hukuk_konulari": len(hukuk.KONULAR),
        "tarife": harc.durum(),
    }


_REF = {"type": "string", "description": "parsel_oku'nun döndürdüğü ref"}
_INLINE = {"type": "boolean", "default": False,
           "description": "true: içerik yanıtta döner (token harcar). Varsayılan: dosyaya yazılır, yol döner."}

TOOLS = [
    Tool("rehber",
         "BURADAN BAŞLAYIN. Veri yolu (TKGM'ye neden bağlanılmaz, parsel dosyası nasıl alınır), "
         "araç akışı ve kapsam dışı kalanlar. ~250 token.",
         {"type": "object", "properties": {}}, _t_rehber),
    Tool("baglanti",
         "Kullanıcının kendi tarayıcısında açacağı Parsel Sorgu bağlantısı (koordinat ya da ref "
         "verilirse doğrudan o noktaya) ve idari sorgu + dosya indirme adımları; ayrıca Web Tapu / "
         "e-Devlet tapu bağlantıları. Sunucu TKGM'ye istek göndermez.",
         {"type": "object", "properties": {
             "enlem": {"type": "number"}, "boylam": {"type": "number"}, "ref": _REF,
             "il": {"type": "string"}, "ilce": {"type": "string"}, "mahalle": {"type": "string"},
             "ada": {"type": "string"}, "parsel": {"type": "string"}}},
         _t_baglanti),
    Tool("parsel_oku",
         "Kullanıcının Parsel Sorgu'dan indirdiği GeoJSON/KML'yi okur; her parsel için ref, "
         "öznitelikler, TM 3° düzleminde hesap alanı, dosyadaki alanla fark ve nitelikten doğan "
         "rejim uyarıları (5403, 6831, 3621…) döner. Sonraki bütün araçlar ref ile çalışır.",
         {"type": "object", "properties": {
             "dosya": {"type": "string", "description": "yerel yol (.geojson/.json/.kml); yalnız yerel kipte"},
             "icerik": {"type": "string", "description": "dosyanın metni; paylaşılan sunucuda tek yol"},
             "dom": {"type": "integer", "description": "yalnız dosya projeksiyon koordinatlıysa: 27…45"},
             "sistem": {"type": "string", "enum": ["tm3", "utm6"], "default": "tm3"}}},
         _t_parsel_oku),
    Tool("geometri",
         "Parselin ölçüleri: alan, çevre, köşe sayısı, projeksiyon; ayrinti=true ile kenar boyları, "
         "semtler ve iç açılar (grad). Köşe numaraları kroki ile aynıdır.",
         {"type": "object", "properties": {"ref": _REF, "ayrinti": {"type": "boolean", "default": True}},
          "required": ["ref"]},
         _t_geometri),
    Tool("kroki",
         "A4, milimetre birimli, ÖLÇEKLİ SVG kroki: kenar boyları, köşe numaraları, kuzey oku, ölçek "
         "çubuğu, bilgi bloğu; `komsular` verilirse çevre parseller gri çizilir. Dosyaya yazar, yol "
         "döner (~150 token). Aplikasyon/röperli kroki yerine geçmez.",
         {"type": "object", "properties": {
             "ref": _REF,
             "komsular": {"type": "array", "items": {"type": "string"},
                          "description": "çevre parsellerin ref'leri"},
             "koordinat_tablosu": {"type": "boolean", "default": False},
             "inline": _INLINE},
          "required": ["ref"]},
         _t_kroki),
    Tool("disa_aktar",
         "Parseli GeoJSON, KML, DXF (R12, TM koordinatlı — CAD/NetCAD) veya CSV (köşe listesi) olarak "
         "dosyaya yazar.",
         {"type": "object", "properties": {
             "ref": _REF, "bicim": {"type": "string", "enum": list(BICIMLER)}, "inline": _INLINE},
          "required": ["ref", "bicim"]},
         _t_disa_aktar),
    Tool("koordinat_donustur",
         "Coğrafi (enlem, boylam) ↔ ITRF96 TM 3° (DOM 27-45) ya da UTM 6°. Krüger serisi, dilim içinde "
         "mm-altı. ED50 datum dönüşümü YAPMAZ.",
         {"type": "object", "properties": {
             "yon": {"type": "string", "enum": ["cografi_tm", "tm_cografi"]},
             "noktalar": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                          "description": "cografi_tm: [[enlem, boylam]…]; tm_cografi: [[Y sağa, X yukarı]…]"},
             "dom": {"type": "integer", "description": "dilim orta meridyeni; tm_cografi'de zorunlu"},
             "sistem": {"type": "string", "enum": ["tm3", "utm6"], "default": "tm3"}},
          "required": ["yon", "noktalar"]},
         _t_koordinat),
    Tool("hukuk_koprusu",
         "Parselden hukuka köprü. Argümansız: konu listesi. `konu` ile: dayanak madde ADRESLERİ, parsel "
         "verisinin o uyuşmazlıktaki rolü ve çalıştırılacak tr_mevzuat_ara / tr_ictihat_ara çağrıları. "
         "`ref` ya da `nitelik` ile: niteliğe bağlı özel rejim uyarıları. Hüküm metni vermez.",
         {"type": "object", "properties": {
             "konu": {"type": "string", "enum": list(hukuk.KONULAR)},
             "ref": _REF, "nitelik": {"type": "string"}}},
         _t_hukuk),
    Tool("koridor_kesisim",
         "Güzergâh (hat + genişlik) ile parsellerin kesişimi: her parselden kesilen alan (m², %), eksenin "
         "parsel içindeki boyu, yalnız tamponla dokunulan parseller. İrtifak/kamulaştırma hesabının ham "
         "verisi. Sonuç ölçülmüş sayısal hata payıyla döner; tam tablo CSV'ye yazılır.",
         {"type": "object", "properties": {
             "refs": {"type": "array", "items": {"type": "string"}, "description": "parsel ref'leri"},
             "hat": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                     "description": "[[enlem, boylam]…] — eksen köşe noktaları"},
             "hat_icerik": {"type": "string", "description": "GeoJSON LineString ya da KML metni"},
             "hat_dosya": {"type": "string", "description": "yerel .geojson/.kml; yalnız yerel kipte"},
             "genislik_m": {"type": "number", "description": "koridor TOPLAM genişliği (m)"}},
          "required": ["refs", "genislik_m"]},
         _t_koridor),
    Tool("harita",
         "OpenStreetMap altlıklı etkileşimli HTML harita: parsel vurgulu, `komsular` gri. Sunucu ağ çağrısı "
         "yapmaz; karoları kullanıcının tarayıcısı yükler. Dosyaya yazar, yol döner.",
         {"type": "object", "properties": {
             "ref": _REF, "komsular": {"type": "array", "items": {"type": "string"}}, "inline": _INLINE},
          "required": ["ref"]},
         _t_harita),
    Tool("arazi_3d",
         "Copernicus GLO-30 yükseklik modeli üstüne parsel: min/max/ortalama kot, kot farkı, ortalama eğim %, "
         "hâkim bakı + döndürülebilir 3D HTML. 30 m'lik YÜZEY modelidir (bina/ağaç dâhil); küçük kent "
         "parsellerinde eğim kabadır, ölçü yerine geçmez. ~1-2 MB indirir. Yalnız yerel kipte.",
         {"type": "object", "properties": {
             "ref": _REF, "komsular": {"type": "array", "items": {"type": "string"}}, "inline": _INLINE},
          "required": ["ref"]},
         _t_arazi_3d),
    Tool("parsel_foyu",
         "Tek parsel için Word föyü (.docx): kroki görseli, öznitelik ve ölçü tabloları, kenar tablosu, "
         "hukuki notlar (`konu` verilirse o köprünün dayanak adresleri). Yalnız yerel kipte.",
         {"type": "object", "properties": {
             "ref": _REF, "komsular": {"type": "array", "items": {"type": "string"}},
             "konu": {"type": "string", "enum": list(hukuk.KONULAR)}},
          "required": ["ref"]},
         _t_foy),
    Tool("portfoy_tablosu",
         "Çok parsel için Excel dökümü (.xlsx): konum, ada/parsel, nitelik, alanlar, fark, çevre, merkez, "
         "rejim uyarıları, Parsel Sorgu bağlantısı. Yalnız yerel kipte.",
         {"type": "object", "properties": {"refs": {"type": "array", "items": {"type": "string"}}},
          "required": ["refs"]},
         _t_portfoy),
    Tool("tarife_ara",
         "2026 tapu harcı ((4) sayılı tarife) ve TKGM döner sermaye cetvelinde işlem arar; kod, oran/tutar ve "
         "YK/M işaretini döner. Rakamlar kaynak belgeden birebir alıntıyla tutulur.",
         {"type": "object", "properties": {"sorgu": {"type": "string"}}, "required": ["sorgu"]},
         _t_tarife_ara),
    Tool("harc_hesapla",
         "Tapu harcı ve/veya döner sermaye bedeli ön hesabı; her kalem dayanağı ve alıntısıyla. Nispi harçta "
         "matrah emlak vergisi değerinin altına inmez. Tapu işlemlerinin döner sermaye bedeli yöresel katsayı "
         "ister; verilmezse araç UYDURMAZ, nereden alınacağını söyler.",
         {"type": "object", "properties": {
             "tapu_harci_islem": {"type": "string", "description": "tarife_ara'dan kod, örn. satis, ipotek"},
             "bedel": {"type": "number", "description": "beyan edilen bedel / ipotekte borç miktarı (TL)"},
             "emlak_vergi_degeri": {"type": "number"},
             "doner_sermaye_kod": {"type": "string", "description": "tarife_ara'dan kod, örn. 1.1.2.1"},
             "yoresel_katsayi": {"type": "number", "description": "ilgili tapu müdürlüğünün katsayısı"},
             "adet": {"type": "integer", "default": 1, "description": "taşınmaz / bağımsız bölüm adedi"}}},
         _t_harc),
    Tool("tapu_kaydi_oku",
         "Kullanıcının KENDİ e-Devlet/Web Tapu oturumundan aldığı tapu kaydı METNİNİ yapılandırır: taşınmaz, "
         "malik-hisse, şerh/beyan/irtifak/rehin, hukuki işaretler; `ref` verilirse geometriyle çapraz kontrol. "
         "T.C. kimlik no maskelenir, hiçbir şey saklanmaz. Yalnız yerel kipte. Sunucu hiçbir oturuma girmez; "
         "PDF'i siz okuyup metnini verin.",
         {"type": "object", "properties": {
             "metin": {"type": "string"}, "ref": _REF,
             "tc_maskele": {"type": "boolean", "default": True}},
          "required": ["metin"]},
         _t_tapu),
    Tool("server_status", "Çalışma kipi, çıktı klasörü, bellekteki parsel sayısı, canlı kaynak durumu.",
         {"type": "object", "properties": {}}, _t_status),
]


if __name__ == "__main__":
    run(TOOLS, name="tkgm-mcp", version=__version__, instructions=INSTRUCTIONS)
