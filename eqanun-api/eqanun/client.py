"""Dependency-free client for Azerbaijan's official legislation API (e-qanun.az).

Wraps the public, unauthenticated REST backend at ``https://api.e-qanun.az`` plus
the Referer-guarded static full-text host at ``https://e-qanun.az``. Uses only the
Python standard library so it runs on a stock Python 3.9+ with no pip installs.

API surface was reconstructed by black-box observation of the official frontend
(see ``API.md``); no authentication is used and the client identifies itself by
name rather than impersonating a browser. All content is public legal acts
published by the Ministry of Justice.

Example
-------
    from eqanun import EqanunClient

    c = EqanunClient()
    hits = c.search("mülki məcəllə", scope="title", status="in_force", length=5)
    print(hits["total"], "results")
    act = c.get_act(hits["results"][0]["id"])
    text = c.get_act_fulltext(act["id"])
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Union

from ._html import decode_html, html_to_text

API_BASE = "https://api.e-qanun.az"
SITE_BASE = "https://e-qanun.az"

__version__ = "0.1.0"

# The static full-text host runs an nginx hotlink guard that requires a same-site
# Referer: a bare GET returns 403, a GET with `Referer: https://e-qanun.az/`
# returns 200. A browser-looking User-Agent is *not* required (verified: an
# identifying UA + Referer -> 200; a full Chrome UA with no Referer -> 403), so
# this client identifies itself honestly instead of impersonating a browser.
# Override with EQANUN_USER_AGENT or the `user_agent=` argument.
_DEFAULT_UA = f"eqanun-api/{__version__} (+https://github.com/beerbottle90/arthurlegal-mcp)"

# Confirmed status filter values (map to the site's Qüvvədədir / Ləğv / Bütün).
STATUS = {"all": 0, "in_force": 1, "cancelled": 2}


class EqanunError(RuntimeError):
    """Raised for transport errors or non-2xx API responses."""


class EqanunClient:
    """Thin, polite client over the e-qanun.az public API."""

    def __init__(
        self,
        *,
        user_agent: Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 2,
        retry_backoff: float = 1.5,
    ) -> None:
        # Precedence: explicit argument > EQANUN_USER_AGENT > library default.
        # Reading the env var here means the CLI and the MCP server inherit it
        # without either of them needing a flag of its own.
        self.user_agent = user_agent or os.environ.get("EQANUN_USER_AGENT") or _DEFAULT_UA
        self.timeout = timeout
        self.retries = retries
        self.retry_backoff = retry_backoff

    # ---------------------------------------------------------------- transport
    def _request(self, url: str, *, referer: Optional[str] = None) -> bytes:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        }
        if referer:
            headers["Referer"] = referer

        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                # 4xx are deterministic; do not retry those.
                if 400 <= exc.code < 500:
                    raise EqanunError(f"HTTP {exc.code} for {url}") from exc
                last_exc = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
            if attempt < self.retries:
                time.sleep(self.retry_backoff * (attempt + 1))
        raise EqanunError(f"request failed for {url}: {last_exc}") from last_exc

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{API_BASE}{path}"
        if params:
            # doseq keeps repeated keys (e.g. categories=0&categories=1).
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        raw = self._request(url, referer=f"{SITE_BASE}/")
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise EqanunError(f"invalid JSON from {url}") from exc

    # ------------------------------------------------------------------- search
    def search(
        self,
        query: str,
        *,
        scope: str = "title",
        status: str = "in_force",
        exact: bool = False,
        types: Optional[Iterable[Union[int, str]]] = None,
        start: int = 0,
        length: int = 20,
        order_column: int = 8,
        order_direction: str = "desc",
    ) -> Dict[str, Any]:
        """Search legal acts by word.

        Parameters
        ----------
        query : the search term(s).
        scope : ``"title"`` searches act titles, ``"text"`` searches full text.
        status : one of ``"in_force"``, ``"cancelled"``, ``"all"``.
        exact : exact-match toggle.
        types : optional act-type ids from :meth:`list_types` — restricts the
            search to those types. ``None`` (default) means no type filter.
            Example: ``types=[73]`` returns only Constitutional Court decisions.
        start / length : pagination (offset, page size).

        Returns a dict ``{"total": int, "start": int, "length": int,
        "results": [act, ...]}`` where each act has id, title, citation,
        typeName, statusName, acceptDate, registerCode, ...

        Note: ``length``, ``order_column`` and the ``array`` parameter this
        builds from ``types`` are mandatory upstream — omitting any of the three
        makes ``/getDetailSearch`` return HTTP 500.
        """
        if scope not in ("title", "text"):
            raise ValueError("scope must be 'title' or 'text'")
        if status not in STATUS:
            raise ValueError(f"status must be one of {sorted(STATUS)}")

        if types is None:
            array = ""
        else:
            try:
                ids = [int(t) for t in types]
            except (TypeError, ValueError) as exc:
                raise ValueError("types must be an iterable of act-type ids (int)") from exc
            array = ",".join(str(i) for i in ids)

        params = {
            "start": start,
            "length": length,
            "orderColumn": order_column,
            "orderDirection": order_direction,
            "name": query,
            "title": "true" if scope == "title" else "false",
            "text": "true" if scope == "text" else "false",
            "exact": "true" if exact else "false",
            "statusId": STATUS[status],
            "codeType": 1,
            "dateType": 1,
            "secondType": 2,
            "specialDate": "false",
            "array": array,
        }
        data = self._get_json("/getDetailSearch", params)
        results = data.get("data") or []
        return {
            "total": data.get("totalCount"),
            "start": start,
            "length": length,
            "results": results,
        }

    def count(
        self,
        query: str,
        *,
        scope: str = "title",
        status: str = "in_force",
        exact: bool = False,
    ) -> int:
        """Return only the number of acts matching a query (cheap).

        There is no ``types`` filter here: ``/detailNameCount`` ignores the
        ``array`` parameter (verified — the count is identical with and without
        it). For a type-filtered count use ``search(..., types=[...], length=1)``
        and read ``["total"]``.
        """
        if scope not in ("title", "text"):
            raise ValueError("scope must be 'title' or 'text'")
        if status not in STATUS:
            raise ValueError(f"status must be one of {sorted(STATUS)}")

        params = {
            "name": query,
            "title": "true" if scope == "title" else "false",
            "text": "true" if scope == "text" else "false",
            "statusId": STATUS[status],
            "exact": "true" if exact else "false",
        }
        data = self._get_json("/detailNameCount", params)
        # This endpoint returns the count in the `id` field.
        n = data.get("id")
        if not isinstance(n, int):
            # Never return None silently — a missing count would otherwise be
            # printed as `null` and could be quoted as a corpus figure.
            raise EqanunError(f"unexpected /detailNameCount response: {data!r}")
        return n

    # -------------------------------------------------------------------- acts
    def get_act(self, act_id: int) -> Dict[str, Any]:
        """Return metadata for one act, flattened.

        Includes ``htmlUrl`` (canonical full-text location) and the requisite
        fields (title, acceptDate, effectDate, typeName, statusName, classCodes).
        """
        data = self._get_json(f"/framework/{int(act_id)}")
        payload = data.get("data") or {}
        req = payload.get("requisite") or {}
        return {
            "id": payload.get("id"),
            "htmlUrl": payload.get("htmlUrl"),
            "title": req.get("title"),
            "acceptDate": req.get("acceptDate"),
            "effectDate": req.get("effectDate"),
            "typeName": req.get("typeName"),
            "statusName": req.get("statusName"),
            "citation": req.get("citation"),
            "registerCode": req.get("registerCode"),
            "registerDate": req.get("registerDate"),
            "classCodes": req.get("classCodes"),
            "_raw": payload,
        }

    def get_act_fulltext(self, act_id: int, *, as_text: bool = True) -> str:
        """Fetch the full text of an act.

        Resolves the act's ``htmlUrl`` from metadata, then fetches that static
        HTML with the same-site ``Referer`` the host's hotlink guard requires.
        By default the HTML is converted to clean plain text; pass
        ``as_text=False`` for raw HTML.
        """
        meta = self.get_act(act_id)
        html_url = meta.get("htmlUrl")
        if not html_url:
            raise EqanunError(f"act {act_id} has no htmlUrl")
        raw = self._request(html_url, referer=f"{SITE_BASE}/")
        html = decode_html(raw)
        return html_to_text(html) if as_text else html

    # ------------------------------------------------------------------ lookups
    def list_types(self) -> List[Dict[str, Any]]:
        """Return the act-type taxonomy tree (id, name w/ counts, parentId)."""
        data = self._get_json("/all-type")
        return data.get("data") or []

    def list_sections(self) -> List[Dict[str, Any]]:
        """Return the four top-level sections (normative / non-normative / ...)."""
        data = self._get_json("/sections")
        return data.get("data") or []
