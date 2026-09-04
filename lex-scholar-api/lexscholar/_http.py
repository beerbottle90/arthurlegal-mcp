"""Shared, dependency-free HTTP transport for every source adapter.

Uses only the Python standard library so the whole package runs on a stock
Python 3.9 with no pip installs (same constraint as the sibling ``eqanun`` and
``resourcecontracts`` servers).

Adds three things every adapter needs:

- a polite, identifying User-Agent (several upstreams 403 a bare urllib UA);
- retry with backoff on 5xx / transport errors, but never on 4xx;
- rate-limit header capture, so the client can see how much budget a metered
  source (notably OpenAlex) has left and stop calling it before it 429s.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

# Some upstreams (bepress/Digital Commons, SciELO) reject non-browser agents,
# so we look browser-ish while still identifying the caller and a contact.
#
# Do NOT add a "+https://..." crawler URL here. DOAJ pattern-matches that
# convention and answers 403 to it (verified: identical request returns 200
# once the +URL token is removed), which would silently disable the spine of
# the whole federation. A bare mailto is accepted everywhere.
DEFAULT_UA = "Mozilla/5.0 (compatible; lex-scholar-mcp/0.1; mailto:demirertug@gmail.com)"

# Contact address sent to polite pools (Crossref, Unpaywall, OpenAlex).
DEFAULT_MAILTO = "demirertug@gmail.com"


class LexScholarError(RuntimeError):
    """Transport error, non-2xx response, or unparseable payload."""


class RateLimited(LexScholarError):
    """Upstream answered 429. The caller should drop this source, not retry."""


# Last-seen rate-limit budget per host, populated from response headers.
# OpenAlex reports X-RateLimit-Remaining; we use it to self-throttle.
_BUDGET: Dict[str, Dict[str, Any]] = {}


def budget_for(host: str) -> Dict[str, Any]:
    """Return the last-seen rate-limit budget for a host (may be empty)."""
    return dict(_BUDGET.get(host) or {})


def _record_budget(host: str, headers) -> None:
    remaining = headers.get("X-RateLimit-Remaining")
    limit = headers.get("X-RateLimit-Limit")
    if remaining is None and limit is None:
        return
    entry: Dict[str, Any] = {}
    for key, raw in (("remaining", remaining), ("limit", limit)):
        try:
            entry[key] = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            entry[key] = None
    _BUDGET[host] = entry


def request(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    retries: int = 2,
    backoff: float = 1.5,
    user_agent: str = DEFAULT_UA,
    accept: str = "application/json, text/xml;q=0.9, */*;q=0.8",
) -> bytes:
    """GET ``url`` and return the raw body.

    4xx are deterministic and are raised immediately (429 as :class:`RateLimited`);
    5xx and transport errors are retried with linear backoff.
    """
    hdrs = {"User-Agent": user_agent, "Accept": accept}
    if headers:
        hdrs.update(headers)
    host = urllib.parse.urlsplit(url).netloc

    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                _record_budget(host, resp.headers)
                return resp.read()
        except urllib.error.HTTPError as exc:
            _record_budget(host, exc.headers or {})
            if exc.code == 429:
                raise RateLimited(f"HTTP 429 (rate limited) for {url}") from exc
            if 400 <= exc.code < 500:
                raise LexScholarError(f"HTTP {exc.code} for {url}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    raise LexScholarError(f"request failed for {url}: {last}") from last


def build_url(base: str, path: str = "", params: Optional[Dict[str, Any]] = None) -> str:
    """Join base+path and append params, dropping None/empty values."""
    url = base.rstrip("/") + (path if path.startswith("/") or not path else "/" + path)
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
    return url


def get_json(base: str, path: str = "", params: Optional[Dict[str, Any]] = None, **kw: Any) -> Any:
    """GET and parse JSON."""
    url = build_url(base, path, params)
    raw = request(url, **kw)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise LexScholarError(f"invalid JSON from {url}") from exc


def get_text(base: str, path: str = "", params: Optional[Dict[str, Any]] = None, **kw: Any) -> str:
    """GET and decode as text (UTF-8 with replacement)."""
    raw = request(build_url(base, path, params), **kw)
    return raw.decode("utf-8", "replace")
