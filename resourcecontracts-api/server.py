#!/usr/bin/env python3
"""MCP server entry point for the ResourceContracts.org API.

Dependency-free: implements MCP in pure standard library (see
``resourcecontracts/mcp_server.py``), so it runs on a stock Python 3.9 — no
``mcp`` SDK, no pip installs. No authentication (the upstream API is public and
this server adds none), so it can be connected straight from a client UI ("No
auth").

Transports
----------
    # local stdio (default) — for a desktop MCP client config
    python3 server.py

    # remote, no-auth Streamable HTTP — connect this URL from a connector UI:
    #   http://<host>:<port>/mcp
    python3 server.py --transport http --host 0.0.0.0 --port 8000

Env fallbacks: RC_MCP_TRANSPORT, RC_MCP_HOST, RC_MCP_PORT.

Tools: search_contracts, count_contracts, get_contract_metadata,
get_contract_text, get_contract_annotations, list_countries, list_resources,
list_years, list_annotation_categories.
"""

from __future__ import annotations

import argparse
import os

from resourcecontracts.mcp_server import run_http, run_stdio


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="resourcecontracts-api MCP server (no auth, stdlib only)"
    )
    p.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("RC_MCP_TRANSPORT", "stdio"),
        help="stdio (default) or http (Streamable HTTP at /mcp)",
    )
    p.add_argument("--host", default=os.environ.get("RC_MCP_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("RC_MCP_PORT", "8000")))
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    if args.transport == "http":
        run_http(args.host, args.port)
    else:
        run_stdio()


if __name__ == "__main__":
    main()
