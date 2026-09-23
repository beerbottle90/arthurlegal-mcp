"""GİB — Gelir İdaresi Başkanlığı özelgeleri (gib.gov.tr portal API).

    POST https://gib.gov.tr/api/gibportal/mevzuat/ozelge/list
         ?page=0&size=10&sortFieldName=ozelgeTarih&sortType=DESC
    body {"status": 2, "deleted": false, "ktype": 99, "title": q, "kanunNo": q, "description": q,
          "ozelgeNo": …, "kanunIds": [..], "ozelgeStartDate": ISO, "ozelgeEndDate": ISO}

The front end puts the same string in title/kanunNo/description and the backend
ORs across them. Each hit carries the ruling HTML inline (``ozelge``/``icerik``),
so ``get`` is a filtered list call by ``ozelgeNo``.

Citation contract: ``GİB <Vergi Dairesi Başkanlığı>, <tarih> tarih ve <sayı> sayılı özelge``.

Endpoint from saidsurucu/yargi-mcp (MIT).
"""

from __future__ import annotations

from typing import Any, Dict, List

from net import Http, HttpError
from textx import html_to_text, paginate, strip_tags
from sources import Source

BASE = "https://gib.gov.tr/api"
_http = Http(BASE, {"Accept": "application/json"})
LIST = "/gibportal/mevzuat/ozelge/list"


def _iso(v: str, end: bool = False) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    return v if "T" in v else v + ("T23:59:59.999Z" if end else "T00:00:00.000Z")


def _body(args: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {"status": 2, "deleted": False, "ktype": 99}
    q = (args.get("query") or "").strip()
    if q:
        body["title"] = body["kanunNo"] = body["description"] = q
    if args.get("ozelge_no"):
        body["ozelgeNo"] = str(args["ozelge_no"]).strip()
    if args.get("kanun_id"):
        body["kanunIds"] = [int(args["kanun_id"])]
    if args.get("date_from"):
        body["ozelgeStartDate"] = _iso(args["date_from"])
    if args.get("date_to"):
        body["ozelgeEndDate"] = _iso(args["date_to"], end=True)
    return body


def _summary(it: Dict[str, Any]) -> Dict[str, Any]:
    date = (it.get("ozelgeTarih") or "")[:10]
    no = it.get("ozelgeNo") or ""
    return {
        "id": it.get("id"), "ozelge_no": no, "date": date,
        "title": strip_tags(it.get("title") or "")[:300],
        "kanun": it.get("kanunTitle") or "", "kanun_no": it.get("kanunNo"), "kanun_id": it.get("kanunId"),
        "citation": "GİB, %s tarih ve %s sayılı özelge" % (_tr(date), no) if no else "GİB özelge (%s)" % date,
        "source_url": it.get("siteLink") or "https://gib.gov.tr/gibmevzuat",
    }


def _tr(iso: str) -> str:
    return "%s.%s.%s" % (iso[8:10], iso[5:7], iso[0:4]) if len(iso) >= 10 else iso


def search(args: Dict[str, Any]) -> Dict[str, Any]:
    if not (args.get("query") or args.get("ozelge_no") or args.get("kanun_id")):
        return {"error": "query, ozelge_no veya kanun_id gerekli."}
    page = max(1, int(args.get("page") or 1))
    params = {"page": page - 1, "size": max(1, min(int(args.get("page_size") or 10), 50)),
              "sortFieldName": "ozelgeTarih", "sortType": "DESC"}
    try:
        data = _http.post_json(LIST, _body(args), params=params)
    except HttpError as exc:
        return {"error": str(exc)}
    rc = data.get("resultContainer") or {}
    items = [_summary(i) for i in rc.get("content") or []]
    return {"query": args.get("query") or "", "page": page, "total": rc.get("totalElements"),
            "total_pages": rc.get("totalPages"), "results": items,
            "note": "Tam metin: kurum_karari_getir(kurum='gib', id=ozelge_no)."}


def get(args: Dict[str, Any]) -> Dict[str, Any]:
    no = str(args.get("id") or "").strip()
    if not no:
        return {"error": "id olarak ozelge_no verin."}
    try:
        data = _http.post_json(LIST, {"status": 2, "deleted": False, "ktype": 99, "ozelgeNo": no},
                               params={"page": 0, "size": 3, "sortFieldName": "ozelgeTarih", "sortType": "DESC"})
    except HttpError as exc:
        return {"error": str(exc)}
    rows = (data.get("resultContainer") or {}).get("content") or []
    if not rows:
        return {"error": "Özelge bulunamadı: %s" % no}
    it = rows[0]
    html = it.get("description") or it.get("ozelge") or it.get("icerik") or ""
    text = html_to_text(html) if "<" in html else html
    head = _summary(it)
    header = "%s\nSayı: %s  Tarih: %s\nKanun: %s\nKonu: %s\n\n" % (
        head["citation"], head["ozelge_no"], head["date"], head["kanun"], head["title"])
    out = paginate(header + text, args.get("page") or 1, int(args.get("page_chars") or 8000))
    out.update(head)
    return out


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Konu/başlık/kanun (aynı anda üç alanda aranır)"},
        "ozelge_no": {"type": "string"},
        "kanun_id": {"type": "integer", "description": "GİB kanun kimliği (opsiyonel)"},
        "date_from": {"type": "string", "description": "YYYY-MM-DD"},
        "date_to": {"type": "string", "description": "YYYY-MM-DD"},
        "page": {"type": "integer", "default": 1}, "page_size": {"type": "integer", "default": 10},
    },
}
GET_SCHEMA = {"type": "object", "properties": {"id": {"type": "string", "description": "ozelge_no"},
                                               "page": {"type": "integer", "default": 1},
                                               "page_chars": {"type": "integer", "default": 8000}},
              "required": ["id"]}

SOURCE = Source(
    key="gib", label="GİB — Gelir İdaresi Başkanlığı özelgeleri", kind="kurum",
    notes="18.000+ özelge (KDV, KV, GV, ÖTV, damga…). Özelge bağlayıcı değildir; idarenin görüşüdür — bunu belirtin.",
    search=search, get=get, search_schema=SEARCH_SCHEMA, get_schema=GET_SCHEMA,
    homepage="https://gib.gov.tr/gibmevzuat",
)
