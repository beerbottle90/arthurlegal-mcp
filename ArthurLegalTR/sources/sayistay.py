"""Sayıştay — Genel Kurul, Temyiz Kurulu ve Daire kararları (sayistay.gov.tr DataTables).

    GET  /KararlarGenelKurul  /KararlarTemyiz  /KararlarDaire      → cookie + __RequestVerificationToken
    POST /KararlarGenelKurul/DataTablesList  (and Temyiz / Daire)   → {"data": [...], "recordsTotal": N}
    GET  /KararlarGenelKurul/Detay/<id>/                            → decision HTML

Known limitation (confirmed by the reference implementation in 2026-05): the
upstream WAF intermittently answers POSTs with HTTP 418 for every client,
browsers included. The error is surfaced as such rather than as "no results".

Citation contract: ``Sayıştay Temyiz Kurulu, 12.03.2024 tarih ve 58472 tutanak no.lu karar``
or ``Sayıştay 5. Dairesi, 2023/1234 sayılı ilam``.

DataTables column layout: saidsurucu/yargi-mcp (MIT).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from net import Http, HttpError
from textx import html_to_text, paginate, strip_tags
from sources import Source

BASE = "https://www.sayistay.gov.tr"
_http = Http(BASE, {"Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest"})
KINDS = {
    "genel_kurul": ("/KararlarGenelKurul", "KararlarGenelKurulAra"),
    "temyiz_kurulu": ("/KararlarTemyiz", "KararlarTemyizAra"),
    "daire": ("/KararlarDaire", "KararlarDaireAra"),
}
_tokens: Dict[str, str] = {}


def _token(kind: str) -> str:
    if kind in _tokens:
        return _tokens[kind]
    html = _http.get_text(KINDS[kind][0], headers={"Accept": "text/html,*/*", "X-Requested-With": ""})
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html)
    if not m:
        raise HttpError(0, BASE + KINDS[kind][0], b"CSRF token bulunamadi")
    _tokens[kind] = m.group(1)
    return m.group(1)


def _cols(names: List[str]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for i, n in enumerate(names):
        out += [("columns[%d][data]" % i, n), ("columns[%d][name]" % i, ""),
                ("columns[%d][searchable]" % i, "true"), ("columns[%d][orderable]" % i, "true"),
                ("columns[%d][search][value]" % i, ""), ("columns[%d][search][regex]" % i, "false")]
    return out


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    kind = args.get("decision_type") or "temyiz_kurulu"
    if kind not in KINDS:
        return {"error": "decision_type: genel_kurul | temyiz_kurulu | daire"}
    path, prefix = KINDS[kind]
    start = max(0, int(args.get("start") or 0))
    length = max(1, min(int(args.get("page_size") or 10), 50))
    q = (args.get("query") or "").strip()
    try:
        token = _token(kind)
        form: List[Tuple[str, str]] = [("draw", "1"), ("start", str(start)), ("length", str(length)),
                                       ("search[value]", ""), ("search[regex]", "false")]
        if kind == "genel_kurul":
            form += _cols(["KARARNO", "KARARNO", "KARARTARIH", "KARAROZETI", ""])
            form += [("order[0][column]", "2"), ("order[0][dir]", "desc"),
                     (prefix + ".KARARNO", args.get("decision_no") or ""), (prefix + ".KARAREK", ""),
                     (prefix + ".KARARTARIHBaslangic", args.get("date_from") or "Başlangıç Tarihi"),
                     (prefix + ".KARARTARIHBitis", args.get("date_to") or "Bitiş Tarihi"),
                     (prefix + ".KARARTAMAMI", q)]
        elif kind == "temyiz_kurulu":
            form += _cols(["TEMYIZTUTANAKTARIHI", "ILAMDAIRESI", "YILI", "KAMUIDARESITURU", "ILAMNO",
                           "DOSYANO", "TEMYIZTUTANAKNO", "TEMYIZKARAR", "WEBKARARKONUSU"])
            form += [("order[0][column]", "0"), ("order[0][dir]", "desc"),
                     (prefix + ".ILAMDAIRESI", args.get("chamber") or "Tüm Daireler"),
                     (prefix + ".YILI", args.get("year") or ""),
                     (prefix + ".KARARTRHBaslangic", args.get("date_from") or "Başlangıç Tarihi"),
                     (prefix + ".KARARTRHBitis", args.get("date_to") or "Bitiş Tarihi"),
                     (prefix + ".KAMUIDARESITURU", args.get("authority_type") or "Tüm Kurumlar"),
                     (prefix + ".ILAMNO", args.get("ilam_no") or ""), (prefix + ".DOSYANO", ""),
                     (prefix + ".TEMYIZTUTANAKNO", args.get("decision_no") or ""),
                     (prefix + ".TEMYIZKARAR", q), (prefix + ".WEBKARARKONUSU", args.get("subject") or "Tüm Konular")]
        else:
            form += _cols(["YARGILAMADAIRESI", "KARARTRH", "KARARNO", "ILAMNO", "MADDENO",
                           "KAMUIDARESITURU", "HESAPYILI", "WEBKARARKONUSU", "WEBKARARMETNI"])
            form += [("order[0][column]", "1"), ("order[0][dir]", "desc"),
                     (prefix + ".YARGILAMADAIRESI", args.get("chamber") or "Tüm Daireler"),
                     (prefix + ".KARARTRHBaslangic", args.get("date_from") or "Başlangıç Tarihi"),
                     (prefix + ".KARARTRHBitis", args.get("date_to") or "Bitiş Tarihi"),
                     (prefix + ".ILAMNO", args.get("ilam_no") or ""),
                     (prefix + ".KAMUIDARESITURU", args.get("authority_type") or "Tüm Kurumlar"),
                     (prefix + ".HESAPYILI", args.get("year") or ""),
                     (prefix + ".WEBKARARKONUSU", args.get("subject") or "Tüm Konular"),
                     (prefix + ".WEBKARARMETNI", q)]
        form.append(("__RequestVerificationToken", token))
        text, hdrs, status = _http.post_form(path + "/DataTablesList", form,
                                             headers={"Referer": BASE + path})
    except HttpError as exc:
        if exc.status == 418 or "418" in str(exc):
            return {"error": "Sayıştay WAF isteği engelledi (HTTP 418). Sunucu tarafı kısıt; daha sonra tekrar deneyin.",
                    "upstream_blocked": True}
        return {"error": str(exc)}
    if status == 418 or "<html" in text[:300].lower():
        return {"error": "Sayıştay WAF isteği engelledi (HTTP %d). Sunucu tarafı kısıt; daha sonra tekrar deneyin." % status,
                "upstream_blocked": True}
    try:
        data = json.loads(text)
    except ValueError:
        return {"error": "Sayıştay beklenmeyen yanıt", "raw": text[:400]}
    rows = []
    for r in data.get("data") or []:
        rid = r.get("Id") or r.get("ID") or r.get("id")
        rows.append({"id": rid, "decision_type": kind,
                     **{k.lower(): (strip_tags(str(v))[:400] if isinstance(v, str) else v) for k, v in r.items() if k not in ("Id", "ID", "id")},
                     "citation": _citation(kind, r)})
    return {"query": q, "decision_type": kind, "start": start, "total": data.get("recordsTotal"),
            "filtered": data.get("recordsFiltered"), "results": rows,
            "note": "Metin: kurum_karari_getir(kurum='sayistay', id='<decision_type>:<id>')."}


def _citation(kind: str, r: Dict[str, Any]) -> str:
    if kind == "genel_kurul":
        return "Sayıştay Genel Kurulu, %s tarih ve %s sayılı karar" % (r.get("KARARTARIH") or "", r.get("KARARNO") or "")
    if kind == "temyiz_kurulu":
        return "Sayıştay Temyiz Kurulu, %s tarih ve %s tutanak no.lu karar" % (r.get("TEMYIZTUTANAKTARIHI") or "", r.get("TEMYIZTUTANAKNO") or "")
    return "Sayıştay %s, %s tarih, ilam no %s" % (r.get("YARGILAMADAIRESI") or "Dairesi", r.get("KARARTRH") or "", r.get("ILAMNO") or "")


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    ref = str(args.get("id") or "").strip()
    kind, _, rid = ref.partition(":")
    if kind not in KINDS or not rid:
        return {"error": "id biçimi '<decision_type>:<id>' olmalı, örn. 'temyiz_kurulu:12345'."}
    url = "%s%s/Detay/%s/" % (BASE, KINDS[kind][0], rid)
    try:
        html = _http.get_text(url, headers={"Accept": "text/html,*/*", "X-Requested-With": ""})
    except HttpError as exc:
        return {"error": str(exc)}
    m = re.search(r'<div[^>]*class="[^"]*(?:karar|content|detay)[^"]*"[^>]*>(.*)', html, re.S | re.I)
    text = html_to_text(m.group(1) if m else html)
    out = paginate(text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update({"id": ref, "source_url": url})
    return out


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Karar metni / özeti içinde arama"},
        "decision_type": {"type": "string", "enum": list(KINDS), "default": "temyiz_kurulu"},
        "chamber": {"type": "string", "description": "Daire, örn. '5. Daire'"},
        "year": {"type": "string"}, "decision_no": {"type": "string"}, "ilam_no": {"type": "string"},
        "authority_type": {"type": "string"}, "subject": {"type": "string"},
        "date_from": {"type": "string", "description": "DD.MM.YYYY"}, "date_to": {"type": "string"},
        "start": {"type": "integer", "default": 0}, "page_size": {"type": "integer", "default": 10},
    },
}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string"}, "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="sayistay", label="Sayıştay — Genel Kurul / Temyiz Kurulu / Daire kararları", kind="kurum",
    notes="Kamu harcama denetimi içtihadı. Upstream WAF zaman zaman 418 döner; 'upstream_blocked' alanını boş sonuçla karıştırmayın.",
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage=BASE + "/KararlarTemyiz",
)
