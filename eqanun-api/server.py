#!/usr/bin/env python3
"""MCP server entry point for the e-qanun.az legislation API.

Dependency-free: implements MCP in pure standard library (see
``eqanun/mcp_server.py``), so it runs on a stock Python 3.9 — no ``mcp`` SDK,
no pip installs. No authentication (the upstream API is public and this server
adds none), so it can be connected straight from a client UI ("No auth").

Transports
----------
    # local stdio (default) — for a desktop MCP client config
    python3 server.py

    # local HTTP (binds loopback by default)
    python3 server.py --transport http --port 8000

    # remote, no-auth Streamable HTTP — connect this URL from a connector UI:
    #   http://<host>:<port>/mcp
    # --host 0.0.0.0 is an explicit opt-in: this server has NO authentication,
    # so binding all interfaces exposes it to everyone who can reach the port.
    python3 server.py --transport http --host 0.0.0.0 --port 8000

Env fallbacks: EQANUN_MCP_TRANSPORT, EQANUN_MCP_HOST, EQANUN_MCP_PORT,
EQANUN_USER_AGENT.

Tools: search_acts, count_acts, get_act, get_act_fulltext, list_types,
list_sections.
"""

from __future__ import annotations

import argparse
import os

from eqanun.mcp_server import run_http, run_stdio


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="eqanun-api MCP server (no auth, stdlib only)")
    p.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("EQANUN_MCP_TRANSPORT", "stdio"),
        help="stdio (default) or http (Streamable HTTP at /mcp)",
    )
    p.add_argument(
        "--host",
        default=os.environ.get("EQANUN_MCP_HOST", "127.0.0.1"),
        help="bind address (default 127.0.0.1; use 0.0.0.0 to expose the "
             "unauthenticated server on all interfaces — opt in deliberately)",
    )
    p.add_argument("--port", type=int, default=int(os.environ.get("EQANUN_MCP_PORT", "8000")))
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    if args.transport == "http":
        run_http(args.host, args.port)
    else:
        run_stdio()


if __name__ == "__main__":
    main()
