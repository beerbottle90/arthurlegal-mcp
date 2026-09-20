"""Mevzuat — kanun, KHK, CB kararnamesi, yönetmelik, tebliğ … (Bedesten / mevzuat.adalet.gov.tr).

Upstream: ``https://bedesten.adalet.gov.tr/mevzuat`` (same rate limit and same
envelope as the içtihat service; the bucket is shared).

    POST /searchDocuments        title / full-text search, 12 legislation types
    POST /getDocumentContent     {"documentType": "MEVZUAT", "id": mevzuatId}
    POST /mevzuatMaddeTree       article tree (içindekiler)
    POST /getDocumentContent     {"documentType": "MADDE", "id": maddeId}
    POST /getDocumentContent     {"documentType": "GEREKCE", "id": gerekceId}

Citation contract: ``<Kanun adı> (No. 6446, RG 30.03.2013/28603) m. 12/3``.
``mevzuatNo``, ``resmiGazeteTarihi`` and ``resmiGazeteSayisi`` come verbatim from
the record; article numbers come from the madde tree — never from memory.

Ported from saidsurucu/mevzuat-mcp (MIT) — endpoint discovery and envelope are
his; the transport is rewritten on the standard library and the per-type tool
sprawl is collapsed into one ``mevzuat_ara`` with a ``types`` filter.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from net import Http, HttpError
from textx import html_to_text, paginate, pdf_to_text, tr_fold, excerpt, count_hits
from sources import Source
from sources.bedesten_ictihat import BUCKET, HEADERS

try:                                    # Konu süzgeci isteğe bağlıdır.
    import triyaj as _triyaj
except Exception:                       # pragma: no cover - modül yoksa sessiz
    _triyaj = None

BASE = "https://bedesten.adalet.gov.tr/mevzuat"

# Bedesten'in gerçek üst sınırı. 20'den büyük bir pageSize HTTP 400 döndürür:
# "Kayıt sayısı 20'den fazla olamaz" (canlı, 2026-09-20). Şema 50 diyordu.
PAGE_MAX = 20

# Bedesten TEK TARAFLI bir RG tarih aralığını SESSİZCE yok sayar (canlı, 2026-09-20): yalnız
# resmiGazeteTarihiStart verilince 917 kanunun hepsi döner, ikisi birlikte verilince 6. Eksik ucu
# biz doldururuz; yoksa rg_date_from="2024-09-01" diyen çağıran 1926 tarihli kanunları da alır.
_RG_EN_ESKI = "1850-01-01T21:00:00.000Z"
_RG_EN_YENI = "2100-01-01T21:00:00.000Z"

# Bir çağrıda metni açılacak torba kanun sayısı (her biri bir Bedesten isteği).
TORBA_AZAMI = 5
# "(9/6/1932 tarihli ve 2004 sayılı İcra ve İflas Kanunu ile ilgili olup, …)".
# Metinde "ile" ile "ilgili" arasına satır sonu girebiliyor; boşluklar \s+ ile geçilir.
_TORBA_RE = re.compile(r"\(([^()]{5,400}?)\s+ile\s+ilgili\s+olup", re.S)
_JENERIK = {"kanun", "kanun hukmunde kararname"}      # "2872 sayılı Kanun" gibi adsız atıflar

_KONU_NOT = ("Konu süzgeci bir ÖN ELEMEDİR ve Resmî Gazete fihristinde eğitildi. Mevzuat "
             "başlıklarında ayrıca ölçüldü: eğitimde görülmemiş 60 başlıkta vergi 4/4, icra 2/2, "
             "enerji 0/3 yakalandı (pozitif az; sayı okuyun). Enerji taramasında konu'ya "
             "güvenmeyin, query ile birlikte kullanın. Torba kanunlarda değiştirilen kanun "
             "adlarına bakılır; adları okunamayan torba kanun ELENMEZ, konu_kaynak='belirsiz' "
             "ile döner. esik=0 süzgeci kapatır.")
_http = Http(BASE, HEADERS, bucket=BUCKET)

TYPES = {
    "KANUN": "Kanun", "KHK": "Kanun Hükmünde Kararname", "TUZUK": "Tüzük",
    "CB_KARARNAME": "Cumhurbaşkanlığı Kararnamesi", "CB_KARAR": "Cumhurbaşkanı Kararı",
    "CB_YONETMELIK": "Cumhurbaşkanlığı / Bakanlar Kurulu Yönetmeliği",
    "CB_GENELGE": "Cumhurbaşkanlığı Genelgesi", "YONETMELIK": "Bakanlar Kurulu Yönetmeliği",
    "KKY": "Kurum ve Kuruluş Yönetmeliği", "UY": "Üniversite Yönetmeliği",
    "TEBLIGLER": "Tebliğ", "MULGA": "Mülga mevzuat",
}

_cache: Dict[str, Any] = {}


def _wrap(data: Dict[str, Any], paging: bool = False) -> Dict[str, Any]:
    out = {"data": data, "applicationName": "UyapMevzuat"}
    if paging:
        out["paging"] = True
    return out


def _rg_iso(d: str, end: bool = False) -> str:
    """DD/MM/YYYY or YYYY-MM-DD → the UTC boundary the API expects (Turkey is UTC+3)."""
    d = d.strip()
    dt = datetime.strptime(d, "%Y-%m-%d") if "-" in d[:5] else datetime.strptime(d, "%d/%m/%Y")
    if end:
        return dt.strftime("%Y-%m-%dT21:00:00.000Z")
    return (dt - timedelta(days=1)).strftime("%Y-%m-%dT21:00:00.000Z")


def _call(path: str, data: Dict[str, Any], paging: bool = False) -> Any:
    body = _http.post_json(path, _wrap(data, paging))
    meta = body.get("metadata") or {}
    if meta.get("FMTY") != "SUCCESS":
        raise HttpError(400, BASE + path, str(meta.get("FMTE") or meta).encode())
    return body.get("data")


def _satir(m: Dict[str, Any]) -> Dict[str, Any]:
    tur = m.get("mevzuatTur") or {}
    rg_date = (m.get("resmiGazeteTarihi") or "")[:10]
    return {
        "mevzuat_id": m.get("mevzuatId"),
        "number": m.get("mevzuatNo"),
        "title": (m.get("mevzuatAdi") or "").strip(),
        "type": tur.get("name"),
        "tertip": m.get("mevzuatTertip"),
        "rg_date": rg_date,
        "rg_number": m.get("resmiGazeteSayisi"),
        "mukerrer": m.get("mukerrer"),
        "gerekce_id": m.get("gerekceId"),
        "citation": _citation(m, tur, rg_date),
        "source_url": m.get("url") or "",
    }


def _degistirilen(mevzuat_id: str) -> List[str]:
    """Bir torba kanunun değiştirdiği kanun ADLARI (metnin ilk sayfasından)."""
    g = get({"mevzuat_id": mevzuat_id, "page": 1, "page_chars": 12000})
    adlar: List[str] = []
    for mt in _TORBA_RE.finditer(g.get("text") or ""):
        seg = " ".join(mt.group(1).split())
        ad = re.sub(r"^.*?say[ıi]l[ıi]\s+", "", seg)
        if len(ad) < 8 or " ".join(tr_fold(ad).split()) in _JENERIK:
            continue
        if ad not in adlar:
            adlar.append(ad)
    return adlar


def _konu_ele(items: List[Dict[str, Any]], konu: str, motor: Any, e: float):
    """Başlıkları konu olasılığına göre eler; ``(tutulan, belirsiz_sayısı)``.

    Torba kanun ("… Değişiklik Yapılmasına Dair Kanun") başlığı konuyu taşımaz:
    7531 sayılı Kanun İİK ve HMK'yı değiştirir, adı "Bazı Kanunlarda Değişiklik"tir.
    Başlık eşiği geçemezse değiştirilen kanun ADLARI skorlanır. Adlar okunamazsa
    (istek sınırı, hata, metinde ad yok) kalem ELENMEZ: bilinmeyeni atmak, bir
    hukukçuya "bu dönemde İİK değişmedi" demektir.
    """
    tutulan: List[Dict[str, Any]] = []
    belirsiz = acilan = 0
    for it in items:
        baslik = " ".join((it.get("title") or "").split())
        metin = (_triyaj.TUR_BOLUM.get(it.get("type") or "", "") + " " + baslik).strip()
        p = motor.p(konu, metin)
        kaynak, degisen = "baslik", None
        torba = (it.get("type") == "KANUN"
                 and "degisiklik yapilmasina dair kanun" in " ".join(tr_fold(baslik).split()))
        if torba and p < e:
            adlar: List[str] = []
            if acilan < TORBA_AZAMI:
                acilan += 1
                try:
                    adlar = _degistirilen(str(it.get("mevzuat_id") or ""))
                except Exception:  # noqa: BLE001 - okunamayan torba kanun belirsizdir, hata değil
                    adlar = []
            if not adlar:
                kaynak = "belirsiz"
                belirsiz += 1
            else:
                en_p, en_ad = max(((motor.p(konu, "KANUNLAR " + ad), ad) for ad in adlar),
                                  key=lambda t: t[0])
                if en_p > p:
                    p, kaynak, degisen = en_p, "degistirilen_kanunlar", en_ad
        if kaynak == "belirsiz" or p >= e:
            ek: Dict[str, Any] = {"konu_skoru": round(p, 4), "konu_kaynak": kaynak}
            if degisen:
                ek["konu_degistirilen"] = degisen
            tutulan.append(dict(it, **ek))
    return tutulan, belirsiz


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    q = (args.get("query") or "").strip()
    number = str(args.get("number") or "").strip()
    types = args.get("types") or []
    if isinstance(types, str):
        types = [t.strip() for t in types.split(",") if t.strip()]
    bad = [t for t in types if t not in TYPES]
    if bad:
        return {"error": "Bilinmeyen mevzuat türü %s. Geçerli: %s" % (bad, list(TYPES))}
    # Bedesten sorgu kelimesi olmadan da listeler: tür ve/veya RG tarih aralığı yeter
    # (canlı doğrulandı 2026-09-20). Konu taramasını mümkün kılan şey budur.
    listeleme = bool(types or args.get("rg_date_from") or args.get("rg_date_to") or args.get("rg_number"))
    if not q and not number and not listeleme:
        return {"error": "query, number ya da bir liste süzgeci (types / rg_date_from / "
                         "rg_date_to / rg_number) gerekli."}
    konu = (args.get("konu") or "").strip()
    hazir: Any = None
    if konu:                       # doğrulama AĞA ÇIKMADAN
        if _triyaj is None:
            return {"error": "Konu süzgeci bu kurulumda yok (triyaj modülü yüklü değil)."}
        hazir = _triyaj.hazirla(konu, args.get("esik"))
        if isinstance(hazir, dict):
            return hazir
    where = args.get("search_in") or "title"
    page_size = max(1, min(int(args.get("page_size") or 10), PAGE_MAX))
    page = max(1, int(args.get("page") or 1))
    data: Dict[str, Any] = {
        "pageSize": page_size,
        "pageNumber": page,
        "sortFields": ["RESMI_GAZETE_TARIHI"],
        "sortDirection": "desc",
    }
    if q:
        if where == "fulltext":
            data["phrase"] = q
            data["basliktaAra"] = False
        else:
            data["mevzuatAdi"] = q
    if number:
        data["mevzuatNo"] = number
    if types:
        data["mevzuatTurList"] = types
    if args.get("exact_phrase"):
        data["tamCumle"] = True
    try:
        bas = _rg_iso(args["rg_date_from"]) if args.get("rg_date_from") else ""
        son = _rg_iso(args["rg_date_to"], end=True) if args.get("rg_date_to") else ""
    except ValueError:
        return {"error": "Tarih biçimi YYYY-MM-DD veya DD/MM/YYYY olmalı."}
    if bas or son:                 # tek taraflı aralık upstream'de sessizce yok sayılır
        data["resmiGazeteTarihiStart"] = bas or _RG_EN_ESKI
        data["resmiGazeteTarihiEnd"] = son or _RG_EN_YENI
    if args.get("rg_number"):
        data["resmiGazeteSayisi"] = str(args["rg_number"])

    # konu verilince art arda birkaç sayfa taranabilir; aksi hâlde tek sayfa (eski davranış).
    max_pages = max(1, min(int(args.get("max_pages") or 1), 5)) if konu else 1
    items: List[Dict[str, Any]] = []
    total = 0
    taranan = 0
    sayfa_hatasi = ""
    for n in range(max_pages):
        data["pageNumber"] = page + n
        try:
            d = _call("/searchDocuments", data, paging=True) or {}
        except HttpError as exc:
            if n == 0:
                return {"error": str(exc)}
            sayfa_hatasi = "sayfa %d alınamadı: %s" % (page + n, exc)   # eksik ≠ boş
            break
        lst = d.get("mevzuatList") or []
        total = d.get("total", total)
        items.extend(_satir(m) for m in lst)
        taranan += 1
        if len(lst) < page_size:
            break
    out: Dict[str, Any] = {
        "query": q or number or "(liste)", "total": total, "page": page, "results": items,
        "note": "Tam metin: mevzuat_getir(mevzuat_id). Madde ağacı: mevzuat_icindekiler. "
                "Belirli hükmü aramak için mevzuat_icinde_ara."}
    if konu:
        motor, e = hazir
        tutulan, belirsiz = _konu_ele(items, konu, motor, e)
        out.update(results=tutulan, konu=konu, esik=e, konu_taranan=len(items),
                   konu_elenen=len(items) - len(tutulan), konu_belirsiz=belirsiz,
                   pages_scanned=taranan, konu_notu=_KONU_NOT)
        if sayfa_hatasi:
            out["pages_error"] = sayfa_hatasi
    return out


def _citation(m: Dict[str, Any], tur: Dict[str, Any], rg_date: str) -> str:
    title = (m.get("mevzuatAdi") or "").strip()
    no = m.get("mevzuatNo")
    s = title
    if no:
        s += " (%s No. %s" % (TYPES.get(tur.get("name") or "", tur.get("name") or ""), no)
        if rg_date:
            s += ", RG %s/%s" % (_tr_date(rg_date), m.get("resmiGazeteSayisi") or "?")
        s += ")"
    return s


def _tr_date(iso: str) -> str:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso


def _content(doc_type: str, doc_id: str) -> Dict[str, Any]:
    key = "%s:%s" % (doc_type, doc_id)
    if key in _cache:
        return _cache[key]
    d = _call("/getDocumentContent", {"documentType": doc_type, "id": doc_id}) or {}
    raw = base64.b64decode(d.get("content") or b"")
    mime = d.get("mimeType") or "text/html"
    if "pdf" in mime:
        text, _ = pdf_to_text(raw)
    else:
        text = html_to_text(raw.decode("utf-8", "replace"))
    out = {"text": text, "mime": mime}
    if len(_cache) > 64:
        _cache.clear()
    _cache[key] = out
    return out


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(args.get("mevzuat_id") or "").strip()
    if not mid:
        return {"error": "mevzuat_id gerekli (mevzuat_ara sonucundan)."}
    try:
        c = _content("MEVZUAT", mid)
    except HttpError as exc:
        return {"error": str(exc)}
    out = paginate(c["text"], args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"mevzuat_id": mid, "mime_type": c["mime"]})
    return out


def toc(args: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(args.get("mevzuat_id") or "").strip()
    if not mid:
        return {"error": "mevzuat_id gerekli."}
    try:
        d = _call("/mevzuatMaddeTree", {"mevzuatId": mid}) or {}
    except HttpError as exc:
        return {"error": str(exc)}
    nodes: List[Dict[str, Any]] = []

    def walk(n: Dict[str, Any], depth: int) -> None:
        if not isinstance(n, dict):
            return
        title = (n.get("title") or n.get("maddeBaslik") or n.get("description") or "").strip()
        mno = n.get("maddeNo")
        if title or mno:
            nodes.append({"madde_id": n.get("maddeId") or n.get("id"), "madde_no": mno,
                          "title": title[:200], "depth": depth, "gerekce_id": n.get("gerekceId")})
        for ch in n.get("children") or []:
            walk(ch, depth + 1)

    if isinstance(d, list):
        for n in d:
            walk(n, 0)
    else:
        walk(d, 0)
    return {"mevzuat_id": mid, "total": len(nodes), "articles": nodes,
            "note": "Madde metni: mevzuat_madde_getir(madde_id)."}


def article(args: Dict[str, Any]) -> Dict[str, Any]:
    aid = str(args.get("madde_id") or "").strip()
    if not aid:
        return {"error": "madde_id gerekli (mevzuat_icindekiler sonucundan)."}
    try:
        c = _content("MADDE", aid)
    except HttpError as exc:
        return {"error": str(exc)}
    return {"madde_id": aid, "text": c["text"], "mime_type": c["mime"]}


def gerekce(args: Dict[str, Any]) -> Dict[str, Any]:
    gid = str(args.get("gerekce_id") or "").strip()
    if not gid:
        return {"error": "gerekce_id gerekli (mevzuat_ara sonucunda gerekce_id alanı; yoksa gerekçe yayımlanmamış)."}
    key = "GEREKCE:%s" % gid
    if key not in _cache:
        try:
            d = _call("/getGerekceContent", {"gerekceId": gid}) or {}
        except HttpError as exc:
            return {"error": str(exc)}
        raw = base64.b64decode(d.get("content") or b"")
        mime = d.get("mimetype") or d.get("mimeType") or "text/html"
        text = pdf_to_text(raw)[0] if "pdf" in mime else html_to_text(raw.decode("utf-8", "replace"))
        _cache[key] = {"text": text, "mime": mime, "mevzuat_id": d.get("mevzuatId")}
    c = _cache[key]
    out = paginate(c["text"], args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"gerekce_id": gid, "mevzuat_id": c.get("mevzuat_id"), "mime_type": c["mime"]})
    return out


_ART_SPLIT = re.compile(r"(?im)^\s*((?:ek\s+|geçici\s+)?madde\s+\d+[a-zA-Z]?(?:\s*/\s*\d+)?\s*[-–—:.]?)")


def search_within(args: Dict[str, Any]) -> Dict[str, Any]:
    """Split the full text on 'MADDE n' headings and rank articles by hits."""
    mid = str(args.get("mevzuat_id") or "").strip()
    q = (args.get("query") or "").strip()
    if not mid or not q:
        return {"error": "mevzuat_id ve query gerekli."}
    try:
        c = _content("MEVZUAT", mid)
    except HttpError as exc:
        return {"error": str(exc)}
    text = c["text"]
    parts = _ART_SPLIT.split(text)
    chunks: List[Dict[str, Any]] = []
    if len(parts) > 1:
        for i in range(1, len(parts) - 1, 2):
            head = parts[i].strip()
            body = parts[i + 1].strip()
            chunks.append({"heading": head, "body": body})
    else:
        step = 2500
        chunks = [{"heading": "bölüm %d" % (i // step + 1), "body": text[i:i + step]}
                  for i in range(0, len(text), step)]
    terms = [t for t in re.split(r"\s+", q) if len(t) > 1]
    scored = []
    for ch in chunks:
        hits = sum(count_hits(ch["body"] + " " + ch["heading"], t) for t in terms)
        if hits:
            scored.append((hits, ch))
    scored.sort(key=lambda x: -x[0])
    limit = max(1, min(int(args.get("limit") or 8), 30))
    out = [{"heading": ch["heading"], "hits": h, "excerpt": excerpt(ch["body"], terms[0] if terms else q, 300),
            "chars": len(ch["body"])} for h, ch in scored[:limit]]
    return {"mevzuat_id": mid, "query": q, "articles_scanned": len(chunks), "matches": len(scored),
            "results": out,
            "note": "Kavramsal (anahtar kelime paylaşmayan) hüküm için semantik_ara kullanın; "
                    "burada yalnız kelime eşleşmesi vardır."}


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Mevzuat adı (varsayılan) veya tam metin ifadesi."},
        "number": {"type": "string", "description": "Mevzuat numarası, örn. 6446"},
        "types": {"type": "array", "items": {"type": "string", "enum": list(TYPES)},
                  "description": "Tür filtresi. Boş = tümü."},
        "search_in": {"type": "string", "enum": ["title", "fulltext"], "default": "title"},
        "exact_phrase": {"type": "boolean", "default": False},
        "rg_date_from": {"type": "string", "description": "Resmî Gazete tarihi ≥, YYYY-MM-DD"},
        "rg_date_to": {"type": "string", "description": "Resmî Gazete tarihi ≤, YYYY-MM-DD"},
        "rg_number": {"type": "string", "description": "Resmî Gazete sayısı"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": PAGE_MAX, "default": 10,
                      "description": "Bedesten'in üst sınırı 20'dir; fazlası 400 döndürür."},
        "konu": {"type": "string", "enum": ["enerji", "rekabet", "vergi", "icra"],
                 "description": "Yerel, ağsız konu ÖN ELEMESİ: başlıkta konu adı geçmese de yakalar; torba "
                                "kanunlarda değiştirilen kanun adlarına da bakar. Sorgu kelimesi gerekmez: "
                                "types ve/veya rg_date_from/rg_date_to ile listeleyin. SINIR: eğitimde "
                                "görülmemiş 60 mevzuat başlığında vergi 4/4, icra 2/2, enerji 0/3 yakalandı — "
                                "enerji taramasında query ile birlikte kullanın. Elenen ve belirsiz sayısı "
                                "yanıtta yazar; esik=0 süzgeci kapatır."},
        "esik": {"type": "number", "description": "Konu eşiği (varsayılan 0.20, ölçülmüştür). 0 = eleme yok, "
                                                   "skorlar yine döner."},
        "max_pages": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1,
                      "description": "Yalnız konu ile: art arda taranacak sayfa sayısı (sayfa başı en çok "
                                     "20 kayıt, istek başına ~3,5 sn)."},
    },
}

GET_SCHEMA = {
    "type": "object",
    "properties": {
        "mevzuat_id": {"type": "string"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_chars": {"type": "integer", "default": 8000},
    },
    "required": ["mevzuat_id"],
}

SOURCE = Source(
    key="mevzuat",
    label="Mevzuat — kanun, KHK, CBK, yönetmelik, tebliğ (Bedesten)",
    kind="mevzuat",
    notes=(
        "12 mevzuat türü tek uçta. Varsayılan arama BAŞLIKTA; hüküm metni için "
        "search_in='fulltext' veya önce mevzuat_ara ile belgeyi bulup mevzuat_icinde_ara. "
        "Madde numarası ve RG künyesi kayıttan gelir; ezberden madde numarası yazmayın. "
        "Sektörel ikincil düzenleme (EPDK/SPK/BDDK tebliğ ve yönetmelikleri) de buradadır: "
        "types=['KKY','TEBLIGLER'] ve query='Enerji Piyasası' gibi. Sorgu kelimesi olmadan da "
        "listeler (types ve/veya RG tarih aralığı); konu=… bu listeyi yerel olarak eler ve torba "
        "kanunlarda değiştirilen kanun adlarına bakar. Sayfa başı en çok 20 kayıt."
    ),
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage="https://mevzuat.adalet.gov.tr",
)
