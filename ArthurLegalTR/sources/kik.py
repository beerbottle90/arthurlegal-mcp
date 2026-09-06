"""KİK — Kamu İhale Kurulu kararları (EKAP v2 JSON API, ekapv2.kik.gov.tr).

    POST /b_ihalearaclari/api/KurulKararlari/GetKurulKararlari      uyuşmazlık kararları
    POST /b_ihalearaclari/api/KurulKararlari/GetKurulKararlariDk    düzenleyici kararlar
    POST /b_ihalearaclari/api/KurulKararlari/GetKurulKararlariMk    mahkeme kararları
    POST /b_ihalearaclari/api/KurulKararlari/GetSorgulamaUrl        {"sorguSayfaTipi": 2}

Request signing (read from the Angular bundle ``1959.*.js``, 2026-09): every
call carries

    X-Ekap-Sec-3  <guid>
    X-Ekap-Sec-1  AES-CBC(guid)                 key = environment.r8fact, IV random
    X-Ekap-Sec-2  base64(IV)
    X-Ekap-Sec-4  AES-CBC(str(epoch_ms))        server time; stale → 401
    X-Ekap-Sec-5  AES-CBC(METHOD)               e.g. "POST"
    X-Ekap-Sec-6  AES-CBC(path)                 e.g. "/b_ihalearaclari/api/…"

The reference implementation (2026-05) used the older ``X-Custom-Request-*``
names with the same scheme. The key ships in the public bundle and rotates
with deployments; it is read from ``KIK_R8FACT`` so a rotation is a config
change, not a code change. Document ids are AES-256-CBC encrypted to a 64-hex
``KararId``. :mod:`aes_min` provides the cipher — no compiled dependency.

Honest status: when the key on file is stale the API answers HTTP 401
``İstek doğrulanamadı (HataKodu 1200)``; that is surfaced as
``upstream_blocked`` with the hint, never as an empty result.

TLS: the host only negotiates with an explicit OpenSSL cipher list.

Citation contract: ``KİK, 12.04.2025 tarih ve 2025/UH.II-1801 sayılı karar``.

Protocol reverse-engineering: saidsurucu/yargi-mcp (MIT).
"""

from __future__ import annotations

import base64
import os
import time
import uuid as _uuid
from typing import Any, Dict, List

from aes_min import aes_cbc_encrypt
from net import Http, HttpError
from textx import html_to_text, paginate, pdf_to_text
from sources import Source

BASE = "https://ekapv2.kik.gov.tr"
_http = Http(BASE, {
    "Accept": "application/json", "Origin": BASE,
    "Referer": BASE + "/sorgulamalar/kurul-kararlari", "api-version": "v1",
}, verify=False, ciphers="DEFAULT:@SECLEVEL=1")

ENDPOINTS = {
    "uyusmazlik": ("/b_ihalearaclari/api/KurulKararlari/GetKurulKararlari", "sorgulaKurulKararlari",
                   "SorgulaKurulKararlariResponse", "SorgulaKurulKararlariResult"),
    "duzenleyici": ("/b_ihalearaclari/api/KurulKararlari/GetKurulKararlariDk", "sorgulaKurulKararlariDk",
                    "SorgulaKurulKararlariDkResponse", "SorgulaKurulKararlariDkResult"),
    "mahkeme": ("/b_ihalearaclari/api/KurulKararlari/GetKurulKararlariMk", "sorgulaKurulKararlariMk",
                "SorgulaKurulKararlariMkResponse", "SorgulaKurulKararlariMkResult"),
}
_DOC_KEY = bytes([236, 193, 164, 43, 12, 135, 121, 170, 4, 244, 123, 219, 82, 158, 124, 174,
                  174, 228, 219, 174, 208, 104, 174, 120, 32, 76, 250, 4, 143, 159, 211, 176])
_SIGN_KEY = os.environ.get("KIK_R8FACT", "Qm2LtXR0aByP69vZNKef4wMJ").encode("utf-8")
_HEADER_STYLE = os.environ.get("KIK_HEADER_STYLE", "ekap")   # ekap | custom
_clock_offset_ms = 0.0


def _sign_headers(method: str, path: str) -> Dict[str, str]:
    guid = str(_uuid.uuid4())
    iv = os.urandom(16)
    ts = str(int(time.time() * 1000 + _clock_offset_ms))
    enc = lambda s: base64.b64encode(aes_cbc_encrypt(_SIGN_KEY, iv, s.encode("utf-8"))).decode()  # noqa: E731
    if _HEADER_STYLE == "custom":
        return {"X-Custom-Request-Guid": guid, "X-Custom-Request-R8id": enc(guid),
                "X-Custom-Request-Siv": base64.b64encode(iv).decode(), "X-Custom-Request-Ts": enc(ts)}
    return {"X-Ekap-Sec-3": guid, "X-Ekap-Sec-1": enc(guid), "X-Ekap-Sec-2": base64.b64encode(iv).decode(),
            "X-Ekap-Sec-4": enc(ts), "X-Ekap-Sec-5": enc(method.upper()), "X-Ekap-Sec-6": enc(path)}


def encrypt_document_id(numeric_id: str) -> str:
    iv = os.urandom(16)
    return iv.hex() + aes_cbc_encrypt(_DOC_KEY, iv, numeric_id.encode()).hex()


def _post(path: str, payload: Any) -> Any:
    return _http.post_json(path, payload, headers=_sign_headers("POST", path))


def _blocked(exc: HttpError) -> Dict[str, Any]:
    if exc.status == 401:
        return {"error": "EKAP v2 isteği doğrulamadı (HTTP 401 — imza anahtarı güncel değil olabilir). "
                         "KIK_R8FACT ortam değişkenini bundle'daki environment.r8fact ile güncelleyin.",
                "upstream_blocked": True, "detail": str(exc)[:200]}
    return {"error": str(exc)}


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    kind = args.get("decision_type") or "uyusmazlik"
    if kind not in ENDPOINTS:
        return {"error": "decision_type: uyusmazlik | duzenleyici | mahkeme"}
    path, wrapper, resp_key, result_key = ENDPOINTS[kind]
    pairs: List[Dict[str, str]] = []
    for key, arg in (("KararMetni", "query"), ("KararNo", "decision_no"), ("BasvuranAdi", "applicant"),
                     ("IdareAdi", "authority"), ("BaslangicTarihi", "date_from"), ("BitisTarihi", "date_to")):
        v = (args.get(arg) or "").strip() if isinstance(args.get(arg), str) else ""
        if v:
            pairs.append({"key": key, "value": v})
    if not pairs:
        return {"error": "En az bir ölçüt: query, decision_no, applicant, authority, date_from/date_to."}
    payload = {wrapper: {"keyValuePairs": {"keyValueOfstringanyType": pairs}}}
    try:
        data = _post(path, payload)
    except HttpError as exc:
        return _blocked(exc)
    result = ((data.get(resp_key) or {}).get(result_key)) or {}
    if result.get("hataKodu") not in (None, "", "0", 0):
        return {"error": "KİK: %s (%s)" % (result.get("hataMesaji"), result.get("hataKodu"))}
    items = []
    for group in result.get("KurulKararTutanakDetayListesi") or []:
        for d in group.get("KurulKararTutanakDetayi") or []:
            date = (d.get("kararTarihi") or "")[:10]
            items.append({
                "id": d.get("gundemMaddesiId"), "decision_no": d.get("kararNo"), "decision_date": date,
                "applicant": d.get("basvuran"), "authority": d.get("idareAdi"),
                "subject": d.get("basvuruKonusu"), "decision_type": kind,
                "nature": d.get("kararNitelik"), "rg": d.get("resmiGazeteTarihi"),
                "citation": "KİK, %s tarih ve %s sayılı karar" % (_tr(date), d.get("kararNo")),
            })
    limit = max(1, min(int(args.get("limit") or 20), 100))
    return {"query": args.get("query") or "", "decision_type": kind, "total": len(items),
            "results": items[:limit], "note": "Metin: kurum_karari_getir(kurum='kik', id=id)."}


def _tr(iso: str) -> str:
    return "%s.%s.%s" % (iso[8:10], iso[5:7], iso[0:4]) if len(iso) >= 10 else iso


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    doc_id = str(args.get("id") or "").strip()
    if not doc_id:
        return {"error": "id (gundemMaddesiId) gerekli."}
    try:
        u = _post("/b_ihalearaclari/api/KurulKararlari/GetSorgulamaUrl", {"sorguSayfaTipi": 2})
        base_url = u.get("sorgulamaUrl") or ""
        if not base_url:
            return {"error": "KİK belge adresi alınamadı", "raw": u}
        karar_id = encrypt_document_id(doc_id) if doc_id.isdigit() else doc_id
        url = "%s?KararId=%s" % (base_url, karar_id)
        body, hdrs, final = _http.get(url, timeout=120)
    except HttpError as exc:
        return _blocked(exc)
    ctype = (hdrs.get("Content-Type") or "").lower()
    if "pdf" in ctype or body.startswith(b"%PDF"):
        text, _ = pdf_to_text(body)
    else:
        text = html_to_text(body.decode("utf-8", "replace"))
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"id": doc_id, "source_url": final})
    return out


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Karar metninde arama"},
        "decision_type": {"type": "string", "enum": list(ENDPOINTS), "default": "uyusmazlik"},
        "decision_no": {"type": "string", "description": "Örn. 2025/UH.II-1801"},
        "applicant": {"type": "string"}, "authority": {"type": "string", "description": "İdare adı"},
        "date_from": {"type": "string", "description": "YYYY-MM-DD"}, "date_to": {"type": "string"},
        "limit": {"type": "integer", "default": 20},
    },
}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string"}, "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="kik", label="KİK — Kamu İhale Kurulu kararları (EKAP v2)", kind="kurum",
    notes=("uyusmazlik (itirazen şikâyet), duzenleyici, mahkeme. API sayfalamaz. İmza anahtarı (KIK_R8FACT) "
           "EKAP dağıtımlarıyla döner; 401 → upstream_blocked, boş sonuç değil."),
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + "/sorgulamalar/kurul-kararlari",
)
