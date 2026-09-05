#!/usr/bin/env python3
"""Client for CELLAR — the Publications Office's semantic repository behind EUR-Lex.

Why not eur-lex.europa.eu? Because its document paths (`legal-content/`,
`search.html`, `eli/`) return a JavaScript shell, not the document. Fetching them
yields the Official Journal date index for every request, which parses as "a
page" and reads as a result. CELLAR is the machine-readable route to the same
corpus, and it is the only one that works from a server.

Full text takes three steps, none of which can be skipped:

  1. https://publications.europa.eu/resource/celex/{CELEX}  -> 303 to a UUID
  2. SPARQL: which manifestations exist for that work in the wanted language
  3. {manifestation}/DOC_1                                   -> the text
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
RESOURCE = "https://publications.europa.eu/resource/celex/"
UA = "ArthurLegal-MCP/1.0 (+https://github.com/beerbottle90/arthurlegal-mcp)"

# ISO 639-3 codes CELLAR uses in its language authority.
LANGS = {
    "en": "ENG", "de": "DEU", "fr": "FRA", "it": "ITA", "es": "SPA",
    "nl": "NLD", "pl": "POL", "pt": "POR", "ro": "RON", "el": "ELL",
    "bg": "BUL", "cs": "CES", "da": "DAN", "et": "EST", "fi": "FIN",
    "ga": "GLE", "hr": "HRV", "hu": "HUN", "lt": "LIT", "lv": "LAV",
    "mt": "MLT", "sk": "SLK", "sl": "SLV", "sv": "SWE",
}

CELEX_RE = re.compile(r"^[0-9][0-9]{4}[A-Z]{1,2}[0-9]{4}(-[0-9]{8})?$|^[0-9A-Z()\-]{6,}$")


class CellarError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102
        return None


def _lang_uri(lang: str) -> str:
    code = LANGS.get((lang or "en").lower())
    if not code:
        raise CellarError("unsupported language %r; use one of: %s"
                          % (lang, ", ".join(sorted(LANGS))))
    return "http://publications.europa.eu/resource/authority/language/%s" % code


def _sparql(query: str, timeout: int = 120) -> Dict[str, Any]:
    url = "%s?query=%s&format=%s" % (
        SPARQL, urllib.parse.quote(query),
        urllib.parse.quote("application/sparql-results+json"))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CellarError("CELLAR SPARQL HTTP %d" % exc.code) from exc
    except urllib.error.URLError as exc:
        raise CellarError("CELLAR unreachable: %s" % exc.reason) from exc
    except ValueError as exc:
        raise CellarError("CELLAR returned non-JSON") from exc


def _rows(data: Dict[str, Any]) -> List[Dict[str, str]]:
    return [{k: v.get("value", "") for k, v in row.items()}
            for row in data.get("results", {}).get("bindings", [])]


def _esc(text: str) -> str:
    """Escape a SPARQL string literal. Unescaped quotes are an injection hole
    and, more prosaically, a syntax error that surfaces as an empty result."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class CellarClient:
    # ---------------------------------------------------------------- search
    def search(self, query: str, lang: str = "en", date_from: str = "",
               date_to: str = "", limit: int = 10) -> Dict[str, Any]:
        if not (query or "").strip():
            raise CellarError("query is required")
        filters = ['FILTER(CONTAINS(LCASE(STR(?title)), "%s"))' % _esc(query.lower())]
        if date_from:
            filters.append('FILTER(?date >= "%s"^^<http://www.w3.org/2001/XMLSchema#date>)'
                           % _esc(date_from))
        if date_to:
            filters.append('FILTER(?date <= "%s"^^<http://www.w3.org/2001/XMLSchema#date>)'
                           % _esc(date_to))
        q = """PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?celex ?title ?date WHERE {
  ?work cdm:resource_legal_id_celex ?celex .
  ?work cdm:work_date_document ?date .
  ?expr cdm:expression_belongs_to_work ?work .
  ?expr cdm:expression_uses_language <%s> .
  ?expr cdm:expression_title ?title .
  %s
} ORDER BY DESC(?date) LIMIT %d""" % (_lang_uri(lang), "\n  ".join(filters),
                                      max(1, min(int(limit), 100)))
        rows = _rows(_sparql(q))
        results = []
        for r in rows:
            celex = r.get("celex", "")
            results.append({
                "celex": celex,
                "title": r.get("title", ""),
                "date": r.get("date", ""),
                "consolidated": "-" in celex and celex.startswith("0"),
                "source_url": "https://eur-lex.europa.eu/legal-content/%s/TXT/?uri=CELEX:%s"
                              % (lang.upper(), celex),
                "citation": "%s (CELEX %s)" % (r.get("title", "")[:120], celex),
            })
        return {"query": query, "language": lang, "returned": len(results),
                "results": results}

    # -------------------------------------------------------------- metadata
    def metadata(self, celex: str, lang: str = "en") -> Dict[str, Any]:
        celex = (celex or "").strip().upper()
        if not celex:
            raise CellarError("celex is required")
        q = """PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT ?title ?date ?eli ?inforce WHERE {
  ?work cdm:resource_legal_id_celex "%s"^^<http://www.w3.org/2001/XMLSchema#string> .
  OPTIONAL { ?work cdm:work_date_document ?date }
  OPTIONAL { ?work cdm:resource_legal_eli ?eli }
  OPTIONAL { ?work cdm:resource_legal_in-force ?inforce }
  ?expr cdm:expression_belongs_to_work ?work .
  ?expr cdm:expression_uses_language <%s> .
  ?expr cdm:expression_title ?title .
} LIMIT 1""" % (_esc(celex), _lang_uri(lang))
        rows = _rows(_sparql(q))
        if not rows:
            raise CellarError(
                "no %s expression for CELEX %s. The document may exist in other "
                "languages only — CELLAR does not translate on demand."
                % (lang.upper(), celex))
        r = rows[0]
        return {
            "celex": celex,
            "title": r.get("title", ""),
            "date": r.get("date", ""),
            "eli": r.get("eli", ""),
            "in_force": r.get("inforce", ""),
            "consolidated": "-" in celex and celex.startswith("0"),
            "language": lang,
            "source_url": "https://eur-lex.europa.eu/legal-content/%s/TXT/?uri=CELEX:%s"
                          % (lang.upper(), celex),
            "citation": "%s (CELEX %s)" % (r.get("title", "")[:160], celex),
        }

    # ------------------------------------------------------------- full text
    def _uuid(self, celex: str) -> str:
        """Step 1: CELEX -> the work's permanent Cellar UUID, via the 303."""
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(RESOURCE + urllib.parse.quote(celex),
                                     headers={"User-Agent": UA})
        try:
            resp = opener.open(req, timeout=60)
            location = resp.headers.get("Location", "")
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location", "") if exc.code in (300, 302, 303) else ""
            if not location:
                raise CellarError("CELEX %s did not resolve (HTTP %d)" % (celex, exc.code)) from exc
        except urllib.error.URLError as exc:
            raise CellarError("CELLAR unreachable: %s" % exc.reason) from exc
        match = re.search(r"cellar/([0-9a-f-]{36})", location)
        if not match:
            raise CellarError("CELEX %s resolved to %r, which carries no UUID"
                              % (celex, location[:120]))
        return match.group(1)

    def manifestations(self, celex: str, lang: str = "en") -> List[str]:
        """Step 2: which manifestations exist for this work in this language."""
        q = """PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT ?manif WHERE {
  ?work cdm:resource_legal_id_celex "%s"^^<http://www.w3.org/2001/XMLSchema#string> .
  ?expr cdm:expression_belongs_to_work ?work .
  ?expr cdm:expression_uses_language <%s> .
  ?manif cdm:manifestation_manifests_expression ?expr .
} LIMIT 20""" % (_esc(celex.upper()), _lang_uri(lang))
        return [r["manif"] for r in _rows(_sparql(q)) if r.get("manif")]

    def text(self, celex: str, lang: str = "en", offset: int = 0,
             max_chars: int = 20000) -> Dict[str, Any]:
        celex = (celex or "").strip().upper()
        meta = self.metadata(celex, lang)
        manifs = self.manifestations(celex, lang)
        if not manifs:
            raise CellarError(
                "no manifestation for CELEX %s in %s" % (celex, lang.upper()))
        # Highest suffix first: .0006.03 is a later rendition than .0006.01.
        manifs.sort(reverse=True)
        last_error = ""
        for manif in manifs[:5]:
            url = manif.replace("http://", "https://") + "/DOC_1"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                break
            except (urllib.error.HTTPError, urllib.error.URLError) as exc:
                last_error = str(exc)
                continue
        else:
            raise CellarError("no manifestation of %s could be fetched (%s)"
                              % (celex, last_error))
        body = re.sub(r"<[^>]+>", " ", raw)
        body = re.sub(r"\s+", " ", body).strip()
        total = len(body)
        offset = max(0, int(offset))
        max_chars = max(500, min(int(max_chars), 100000))
        meta["text"] = body[offset:offset + max_chars]
        meta["total_chars"] = total
        meta["offset"] = offset
        meta["next_offset"] = offset + max_chars if offset + max_chars < total else None
        meta["manifestation"] = manif
        return meta

    def raw_sparql(self, query: str, limit: int = 50) -> Dict[str, Any]:
        if "select" not in query.lower():
            raise CellarError("only SELECT queries are accepted")
        data = _sparql(query)
        rows = _rows(data)[:max(1, min(int(limit), 500))]
        return {"returned": len(rows), "results": rows}
