"""Dependency-free MCP server for the e-qanun.az legislation API.

Implements the Model Context Protocol (JSON-RPC 2.0) over two transports using
only the Python standard library, so it runs on a stock Python 3.9 with no pip
installs — unlike the official ``mcp`` SDK, which requires Python 3.10+.

- **stdio**: line-delimited JSON-RPC on stdin/stdout (local desktop clients).
- **Streamable HTTP**: a single ``/mcp`` endpoint (POST) for remote clients /
  UI connectors. No authentication.

Supported methods: ``initialize``, ``notifications/initialized``, ``ping``,
``tools/list``, ``tools/call``. Tools wrap ``eqanun.EqanunClient``.

This is intentionally minimal: POST requests are answered with a single
``application/json`` JSON-RPC response (allowed by the spec in place of SSE),
and the server does not push server-initiated messages (``GET /mcp`` → 405).
"""

from __future__ import annotations

import functools
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from .client import EqanunClient, EqanunError, __version__
from .retrieval import embeddings_status, semantic_rerank

SERVER_NAME = "eqanun-api"
SERVER_VERSION = __version__

# Cap the JSON-RPC request body: without this a single Content-Length can drive
# the process out of memory.
_MAX_BODY = 1_000_000

# Protocol revisions we can speak; we echo the client's if recognised.
_SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
_DEFAULT_PROTOCOL = "2025-06-18"

_FULLTEXT_PAGE = 20_000

_client = EqanunClient()


# --------------------------------------------------------------------------- #
# Tool definitions: JSON Schema + handler. One source of truth for both        #
# transports.                                                                   #
# --------------------------------------------------------------------------- #
# How many upstream rows to pull before reranking. e-qanun returns matches in
# its own order, so the wanted act is often outside the first page: searching
# for the Civil Code ("Mulki Mecelle") yields 623 rows whose first six are all
# amendment decrees. Reranking a page of 20 cannot fix that -- the Code is not
# in the page. Fetching a wider pool and reordering it can.
_POOL_MULTIPLIER = 5
_POOL_MAX = 200


def _t_search_acts(args: Dict[str, Any]) -> Any:
    length = int(args.get("length", 20))
    start = int(args.get("start", 0))
    rank = bool(args.get("rerank", True))

    pool = min(max(length * _POOL_MULTIPLIER, length), _POOL_MAX) if rank else length
    raw = _client.search(
        args["query"],
        scope=args.get("scope", "title"),
        status=args.get("status", "in_force"),
        exact=bool(args.get("exact", False)),
        types=args.get("types") or None,
        start=start,
        length=pool,
    )
    if not rank:
        raw["ranking"] = "Upstream order, unranked (rerank=false)."
        return raw

    ranked = semantic_rerank(
        args["query"],
        raw.get("results") or [],
        fields=("title", "typeName"),
        limit=length,
    )
    raw["results"] = ranked["results"]
    raw["length"] = len(ranked["results"])
    ranking = {
        "method": ranked["method"],
        "candidates_considered": pool,
        "note": ranked.get("note") or ranked.get("warning"),
    }
    if "model" in ranked:
        ranking["model"] = ranked["model"]
    raw["ranking"] = ranking
    raw["ranking_warning"] = (
        "e-qanun returns matches in its own order, not by relevance. These "
        "results were reordered locally; `total` is still the upstream count. "
        "Pass rerank=false to see the raw upstream order."
    )
    return raw


def _t_count_acts(args: Dict[str, Any]) -> Any:
    return {
        "total": _client.count(
            args["query"],
            scope=args.get("scope", "title"),
            status=args.get("status", "in_force"),
            exact=bool(args.get("exact", False)),
        )
    }


_DISCLAIMER = (
    "statusName and dates are the publisher's own labels, reported as-is. No "
    "independent verification, no amendment history, no point-in-time retrieval; "
    "effectDate is often null. Verify against the canonical htmlUrl before "
    "relying on this."
)


def _t_get_act(args: Dict[str, Any]) -> Any:
    meta = _client.get_act(int(args["act_id"]))
    meta.pop("_raw", None)
    # Carried in the payload so the caveat survives into the model's context,
    # not just the tool listing.
    meta["disclaimer"] = _DISCLAIMER
    return meta


@functools.lru_cache(maxsize=8)
def _fulltext(act_id: int) -> str:
    """Fetch-and-decode one act, memoized for the process lifetime.

    Without this, every page of the character-paginated tool refetches the whole
    act: paging through a ~2M-char code at the 20k default is ~100 downloads of
    a multi-megabyte file from the official host for a single document.
    """
    return _client.get_act_fulltext(act_id)


def _t_get_act_fulltext(args: Dict[str, Any]) -> Any:
    text = _fulltext(int(args["act_id"]))
    offset = int(args.get("offset", 0))
    max_chars = int(args.get("max_chars", _FULLTEXT_PAGE))
    total = len(text)
    chunk = text[offset:offset + max_chars]
    nxt = offset + max_chars
    return {
        "act_id": int(args["act_id"]),
        "offset": offset,
        "returned_chars": len(chunk),
        "total_chars": total,
        "next_offset": nxt if nxt < total else None,
        "text": chunk,
    }


def _t_list_types(args: Dict[str, Any]) -> Any:
    return _client.list_types()


def _t_list_sections(args: Dict[str, Any]) -> Any:
    return _client.list_sections()


_STR = {"type": "string"}
_SCOPE = {"type": "string", "enum": ["title", "text"], "default": "title"}
_STATUS = {"type": "string", "enum": ["in_force", "cancelled", "all"], "default": "in_force"}


def _t_server_status(args: Dict[str, Any]) -> Any:
    """What this server is, and whether semantic ranking is actually live."""
    return {
        "server": "eqanun-api",
        "source": "e-qanun.az (Azerbaijan Ministry of Justice) - public, no auth",
        "mode": "passthrough + local reranking (no local corpus)",
        "known_upstream_quirks": [
            'e-qanun does not rank: a title search for the Civil Code returns 623 rows whose first six are amendment decrees. Results are reranked locally; see `ranking.method` on every search.',
            'Type filtering is the strongest lever: types=[107] (Mecelleler) narrows those 623 rows to 2, the Civil Code first. Types 31 and 32 are the presidential/cabinet decrees that make up most of the noise.',
            'status defaults to in_force, which EXCLUDES repealed acts.',
        ],
        **embeddings_status(),
    }


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_acts",
        "description": (
            "Search Azerbaijani legal acts on e-qanun.az. scope=title searches "
            "titles, scope=text searches full text. status filters in_force / "
            "cancelled / all. types restricts the search to act-type ids from "
            "list_types — e.g. types=[73] returns only Constitutional Court "
            "decisions, [87] Supreme Court Plenum decisions. Returns total count "
            "and results (id, title, citation, typeName, statusName, "
            "acceptDate). Results are RERANKED locally by relevance because "
            "e-qanun returns matches unordered. Use id with get_act. NOTE: status defaults to in_force, "
            "which EXCLUDES repealed acts — pass status='all' for historical "
            "research. statusName and dates are the publisher's own labels; no "
            "point-in-time (as-of-date) retrieval is available."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": _STR,
                "scope": _SCOPE,
                "status": _STATUS,
                "exact": {"type": "boolean", "default": False},
                "types": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Act-type ids from list_types. Omit for no type filter. "
                        "TYPE FILTERING IS THE STRONGEST TOOL HERE: e-qanun mixes "
                        "codes, laws, decrees and cabinet resolutions in one result "
                        "set and does not rank, so an unfiltered search drowns in "
                        "amendment instruments. Ids that matter: 107 = Mecelleler "
                        "(CODES, 27 entries) - use this to find a code itself; "
                        "searching the Civil Code unfiltered returns 623 rows whose "
                        "top hits are all amendment decrees, while types=[107] "
                        "returns 2 with the Civil Code first. 30 = Qanunlar (laws), "
                        "29 = all laws incl. constitutional, 73 = Constitutional "
                        "Court, 87 = Supreme Court Plenum. 31 (presidential decrees) "
                        "and 32 (cabinet resolutions) are the usual amendment noise."
                    ),
                },
                "start": {"type": "integer", "default": 0},
                "length": {"type": "integer", "default": 20},
                "rerank": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Reorder results by relevance before returning them. "
                        "e-qanun does NOT rank: a title search for the Civil "
                        "Code returns 623 rows whose first six are amendment "
                        "decrees. With rerank on, a wider candidate pool is "
                        "fetched and reordered -- semantically when an "
                        "embeddings backend is configured, by BM25 otherwise. "
                        "The response's `ranking.method` says which ran. Set "
                        "false only to inspect the raw upstream order."
                    ),
                },
            },
            "required": ["query"],
        },
        "handler": _t_search_acts,
    },
    {
        "name": "count_acts",
        "description": (
            "Return only the number of acts matching a query (cheap). NOTE: "
            "status defaults to in_force, which EXCLUDES repealed acts — pass "
            "status='all' for historical research. No type filter here (the "
            "upstream count endpoint ignores it); for a type-filtered count use "
            "search_acts with length=1 and read `total`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": _STR,
                "scope": _SCOPE,
                "status": _STATUS,
                "exact": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
        "handler": _t_count_acts,
    },
    {
        "name": "get_act",
        "description": (
            "Return metadata for one act by id (title, dates, type, status, "
            "htmlUrl). statusName and the dates are the PUBLISHER'S OWN LABELS, "
            "reported as-is: there is no independent verification, no amendment "
            "history and no point-in-time (as-of-date) retrieval, and effectDate "
            "is frequently null. Cite from htmlUrl, and state the status and the "
            "date you retrieved it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"act_id": {"type": "integer"}},
            "required": ["act_id"],
        },
        "handler": _t_get_act,
    },
    {
        "name": "get_act_fulltext",
        "description": (
            "Return the full text of an act as clean plain text, paginated by "
            "characters (offset + max_chars). Large acts are millions of chars; "
            "response reports total_chars and next_offset (null at end)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "act_id": {"type": "integer"},
                "offset": {"type": "integer", "default": 0},
                "max_chars": {"type": "integer", "default": _FULLTEXT_PAGE},
            },
            "required": ["act_id"],
        },
        "handler": _t_get_act_fulltext,
    },
    {
        "name": "list_types",
        "description": "Return the act-type taxonomy tree (id, name with counts, parentId).",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_list_types,
    },
    {
        "name": "list_sections",
        "description": "Return the four top-level act sections.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_list_sections,
    },
    {
        "name": "server_status",
        "description": (
            "What this server talks to, whether semantic ranking is "
            "currently live, and the upstream quirks worth defending "
            "against. Call it when results look wrong, to tell a degraded "
            "ranking channel apart from a genuinely empty result set."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_server_status,
    },
]

_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def _public_tools() -> List[Dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in TOOLS
    ]


# --------------------------------------------------------------------------- #
# JSON-RPC dispatch                                                            #
# --------------------------------------------------------------------------- #
def _ok(msg_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _negotiate_protocol(requested: Optional[str]) -> str:
    if requested in _SUPPORTED_PROTOCOLS:
        return requested
    return _DEFAULT_PROTOCOL


def dispatch(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message. Returns a response dict, or None for
    notifications (no id)."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    is_notification = "id" not in message

    if method == "initialize":
        proto = _negotiate_protocol(params.get("protocolVersion"))
        return _ok(msg_id, {
            "protocolVersion": proto,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method in ("notifications/initialized", "initialized"):
        return None  # notification, no response

    if method == "ping":
        return _ok(msg_id, {})

    if method == "tools/list":
        return _ok(msg_id, {"tools": _public_tools()})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            return _err(msg_id, -32602, f"unknown tool: {name}")
        try:
            result = tool["handler"](arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return _ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except (EqanunError, ValueError, KeyError, TypeError) as exc:
            return _ok(msg_id, {
                "content": [{"type": "text", "text": f"error: {exc}"}],
                "isError": True,
            })

    if is_notification:
        return None
    return _err(msg_id, -32601, f"method not found: {method}")


def _handle_payload(payload: Any) -> Tuple[Optional[Any], bool]:
    """Process a parsed JSON-RPC payload (single or batch).

    Returns (response_or_None, had_requests). ``response`` is a dict for a
    single message, a list for a batch, or None when there were only
    notifications.
    """
    if isinstance(payload, list):
        responses = [r for r in (dispatch(m) for m in payload) if r is not None]
        return (responses or None), bool(responses)
    resp = dispatch(payload)
    return resp, resp is not None


# --------------------------------------------------------------------------- #
# stdio transport                                                             #
# --------------------------------------------------------------------------- #
def force_utf8_stdio() -> None:
    """Make stdin/stdout UTF-8 regardless of the platform's default encoding.

    Azerbaijani text (ə, ğ, ı, ş, …) is unencodable in the Windows ANSI
    codepages, and when a desktop MCP client spawns this server stdout is a pipe,
    so Python picks the ANSI codepage rather than UTF-8. Without this, the first
    tool call that returns act text dies with UnicodeEncodeError.
    """
    for stream, kwargs in ((sys.stdout, {"newline": "\n"}), (sys.stderr, {}), (sys.stdin, {})):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", **kwargs)
            except (ValueError, OSError):
                pass


def run_stdio() -> None:
    """Serve MCP over line-delimited JSON-RPC on stdin/stdout."""
    force_utf8_stdio()
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            out.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            out.flush()
            continue
        resp, _ = _handle_payload(payload)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()


# --------------------------------------------------------------------------- #
# Streamable HTTP transport (no auth)                                          #
# --------------------------------------------------------------------------- #
# Browsers attach an Origin header; non-browser MCP clients do not. The MCP
# spec requires local HTTP servers to validate Origin, because otherwise any web
# page the user visits can drive this server through their browser (DNS
# rebinding). Default: reject every browser origin. Opt in with a comma-separated
# EQANUN_MCP_ALLOWED_ORIGINS, or "*" to restore the old permissive behaviour.
_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("EQANUN_MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()
]

_BASE_CORS = {
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Mcp-Session-Id, MCP-Protocol-Version, Authorization, Accept",
    "Access-Control-Expose-Headers": "Mcp-Session-Id",
}


def _origin_allowed(origin: Optional[str]) -> bool:
    """No Origin (non-browser client) is fine; a browser Origin must be allowlisted."""
    if not origin:
        return True
    return "*" in _ALLOWED_ORIGINS or origin in _ALLOWED_ORIGINS


class _MCPHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"
    # The MCP endpoint path (kept small; matched loosely below).
    endpoint = "/mcp"

    def _cors(self) -> Dict[str, str]:
        headers = dict(_BASE_CORS)
        origin = self.headers.get("Origin")
        if origin and _origin_allowed(origin):
            # Reflect only an allowlisted origin — never a blanket wildcard.
            headers["Access-Control-Allow-Origin"] = "*" if "*" in _ALLOWED_ORIGINS else origin
            headers["Vary"] = "Origin"
        return headers

    def _reject_origin(self) -> bool:
        """Send 403 and return True when a browser origin is not allowlisted."""
        origin = self.headers.get("Origin")
        if _origin_allowed(origin):
            return False
        body = json.dumps({
            "error": "origin not allowed",
            "detail": (
                "This MCP server rejects browser origins by default (MCP spec: "
                "DNS-rebinding protection). Set EQANUN_MCP_ALLOWED_ORIGINS to "
                "allow specific origins."
            ),
        }).encode("utf-8")
        self._send(403, body)
        return True

    def _send(self, status: int, body: Optional[bytes] = None,
              content_type: str = "application/json",
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        for k, v in self._cors().items():
            self.send_header(k, v)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body is not None:
            self.wfile.write(body)

    def _send_sse(self, obj: Any, extra: Optional[Dict[str, str]] = None) -> None:
        """Send a single JSON-RPC message as one SSE event, then close.

        Streamable-HTTP clients that prefer text/event-stream (e.g. some
        Copilot Studio / connector clients) get the response this way; both
        this and the application/json path are spec-compliant.
        """
        self.send_response(200)
        for k, v in self._cors().items():
            self.send_header(k, v)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        data = json.dumps(obj, ensure_ascii=False)
        self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _path_ok(self) -> bool:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        return path in (self.endpoint, "/")

    def do_OPTIONS(self) -> None:  # noqa: N802
        if self._reject_origin():
            return
        self._send(204)

    def do_GET(self) -> None:  # noqa: N802
        if self._reject_origin():
            return
        # We do not push server-initiated messages; SSE stream not offered.
        self._send(405, b'{"error":"method not allowed; use POST"}')

    def do_DELETE(self) -> None:  # noqa: N802
        if self._reject_origin():
            return
        # Session termination — accept and succeed.
        self._send(204)

    def do_POST(self) -> None:  # noqa: N802
        if self._reject_origin():
            return
        if not self._path_ok():
            self._send(404, b'{"error":"not found; POST to /mcp"}')
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > _MAX_BODY:
            self._send(413, b'{"error":"request body too large"}')
            return
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._send(400, json.dumps(_err(None, -32700, "parse error")).encode("utf-8"))
            return

        # New session id on initialize (lenient: not strictly enforced after).
        extra: Dict[str, str] = {}
        is_init = (isinstance(payload, dict) and payload.get("method") == "initialize")
        if is_init:
            extra["Mcp-Session-Id"] = os.urandom(16).hex()

        resp, had_requests = _handle_payload(payload)
        if not had_requests:
            # Only notifications/responses -> 202 Accepted, no body.
            self._send(202, extra=extra or None)
            return
        # Respond as SSE if the client prefers it, else plain JSON. Both are
        # allowed by the Streamable HTTP spec for a POST containing requests.
        if "text/event-stream" in self.headers.get("Accept", ""):
            self._send_sse(resp, extra=extra or None)
        else:
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self._send(200, body, extra=extra or None)

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default logging
        pass


class _SingleBindHTTPServer(ThreadingHTTPServer):
    """Refuse to start when the port is already served.

    ``HTTPServer`` sets ``allow_reuse_address = 1``. On Windows that lets a
    SECOND process bind a port another server is already listening on, and
    connections keep going to the first one — so a restarted server silently
    serves stale code while looking healthy. Turning it off makes the second
    start fail loudly with "address already in use", which is the honest answer.
    """

    allow_reuse_address = False


def run_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve MCP over Streamable HTTP (no auth) at http://host:port/mcp."""
    httpd = _SingleBindHTTPServer((host, port), _MCPHandler)
    sys.stderr.write(
        f"{SERVER_NAME} MCP (Streamable HTTP, no auth) on "
        f"http://{host}:{port}/mcp\n"
    )
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
