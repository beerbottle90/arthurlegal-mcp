#!/usr/bin/env python3
"""Client for the GLEIF API — the global register of Legal Entity Identifiers.

https://api.gleif.org/api/v1 — no key, no quota published, JSON:API.

GLEIF answers one question well: *which legal person is this, and who owns it*.
It is not a corporate registry and holds no filings; it holds identity, address,
and the parent/child relationships an entity has self-declared and had validated.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

BASE = "https://api.gleif.org/api/v1"
UA = "ArthurLegal-MCP/1.0 (+https://github.com/beerbottle90/arthurlegal-mcp)"


class GleifError(RuntimeError):
    pass


def _get(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 40) -> Dict[str, Any]:
    url = BASE + path
    if params:
        # JSON:API filter keys carry brackets; quote_via=quote keeps them readable
        # and GLEIF accepts them either way.
        url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.api+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise GleifError("not found: %s" % path) from exc
        raise GleifError("GLEIF HTTP %d on %s" % (exc.code, path)) from exc
    except urllib.error.URLError as exc:
        raise GleifError("GLEIF unreachable: %s" % exc.reason) from exc
    except ValueError as exc:
        raise GleifError("GLEIF returned non-JSON") from exc


def _address(block: Optional[Dict[str, Any]]) -> str:
    if not block:
        return ""
    parts = list(block.get("addressLines") or [])
    for key in ("postalCode", "city", "region", "country"):
        value = block.get(key)
        if value:
            parts.append(value)
    return ", ".join(p for p in parts if p)


def _record(item: Dict[str, Any]) -> Dict[str, Any]:
    a = item.get("attributes") or {}
    entity = a.get("entity") or {}
    reg = a.get("registration") or {}
    name = (entity.get("legalName") or {}).get("name", "")
    lei = a.get("lei") or item.get("id", "")
    return {
        "lei": lei,
        "legal_name": name,
        "other_names": [n.get("name", "") for n in (entity.get("otherNames") or [])],
        "jurisdiction": entity.get("jurisdiction", ""),
        "legal_form": (entity.get("legalForm") or {}).get("id", ""),
        "category": entity.get("category", ""),
        "legal_address": _address(entity.get("legalAddress")),
        "headquarters_address": _address(entity.get("headquartersAddress")),
        "registered_as": entity.get("registeredAs", ""),
        # Two different statuses that are routinely confused. entity_status is
        # about the company; registration_status is about the LEI record. An
        # ACTIVE company can hold a LAPSED LEI -- the entity exists, its
        # reference data has simply not been revalidated.
        "entity_status": entity.get("status", ""),
        "registration_status": reg.get("status", ""),
        "last_update": reg.get("lastUpdateDate", ""),
        "next_renewal": reg.get("nextRenewalDate", ""),
        "managing_lou": reg.get("managingLou", ""),
        "source_url": "https://search.gleif.org/#/record/%s" % lei if lei else "",
        "citation": "%s — LEI %s (GLEIF)" % (name, lei) if name else "LEI %s (GLEIF)" % lei,
    }


class GleifClient:
    def search(self, name: str = "", jurisdiction: str = "", city: str = "",
               status: str = "", page_size: int = 10, page: int = 1) -> Dict[str, Any]:
        params: Dict[str, Any] = {"page[size]": max(1, min(int(page_size), 200)),
                                  "page[number]": max(1, int(page))}
        if name:
            params["filter[entity.legalName]"] = name
        if jurisdiction:
            params["filter[entity.jurisdiction]"] = jurisdiction.upper()
        if city:
            params["filter[entity.legalAddress.city]"] = city
        if status:
            params["filter[entity.status]"] = status.upper()
        if len(params) <= 2:
            raise GleifError("give at least one of name, jurisdiction, city, status")
        data = _get("/lei-records", params)
        meta = (data.get("meta") or {}).get("pagination") or {}
        return {
            "total": meta.get("total", 0),
            "page": meta.get("currentPage", 1),
            "last_page": meta.get("lastPage", 1),
            "golden_copy_date": ((data.get("meta") or {}).get("goldenCopy") or {}).get("publishDate", ""),
            "results": [_record(x) for x in data.get("data", [])],
        }

    def autocomplete(self, query: str, limit: int = 10) -> List[Dict[str, str]]:
        """Name completion. Use it to resolve a rough name to candidate LEIs."""
        if not query.strip():
            raise GleifError("query is required")
        data = _get("/autocompletions", {"field": "fulltext", "q": query})
        out = []
        for item in (data.get("data") or [])[:max(1, int(limit))]:
            a = item.get("attributes") or {}
            # The LEI is a JSON:API relationship, not an attribute: reading it
            # from `attributes` yields an empty string for every hit, which
            # looks like "no LEI on file" rather than "looked in the wrong
            # place".
            rel = ((item.get("relationships") or {}).get("lei-records") or {}).get("data") or {}
            out.append({"value": a.get("value", ""), "lei": rel.get("id", "")})
        return out

    def get(self, lei: str) -> Dict[str, Any]:
        lei = (lei or "").strip().upper()
        if len(lei) != 20:
            raise GleifError("an LEI is exactly 20 characters; got %d" % len(lei))
        data = _get("/lei-records/%s" % urllib.parse.quote(lei))
        return _record(data.get("data") or {})

    def relationships(self, lei: str) -> Dict[str, Any]:
        """Group structure: direct and ultimate parents, and direct children.

        A missing parent is not the same as no parent. GLEIF records three
        distinct reasons an entity reports none -- no legal parent exists, the
        parent has no LEI, or disclosure is legally obstructed -- and the
        endpoint answers 404 in all three cases. The caller is told which
        question went unanswered rather than being handed a bare absence.
        """
        lei = (lei or "").strip().upper()
        if len(lei) != 20:
            raise GleifError("an LEI is exactly 20 characters; got %d" % len(lei))
        out: Dict[str, Any] = {"lei": lei}
        for label, path in (("direct_parent", "direct-parent"),
                            ("ultimate_parent", "ultimate-parent")):
            try:
                data = _get("/lei-records/%s/%s" % (urllib.parse.quote(lei), path))
                out[label] = _record(data.get("data") or {})
            except GleifError as exc:
                out[label] = None
                out[label + "_note"] = (
                    "GLEIF reports no %s. That can mean no legal parent exists, the "
                    "parent holds no LEI, or disclosure is obstructed -- GLEIF does "
                    "not distinguish them here (%s)." % (label.replace("_", " "), exc))
        try:
            data = _get("/lei-records/%s/direct-children" % urllib.parse.quote(lei),
                        {"page[size]": 50})
            out["direct_children"] = [_record(x) for x in data.get("data", [])]
            meta = (data.get("meta") or {}).get("pagination") or {}
            out["direct_children_total"] = meta.get("total", len(out["direct_children"]))
        except GleifError:
            out["direct_children"] = []
            out["direct_children_total"] = 0
        return out
