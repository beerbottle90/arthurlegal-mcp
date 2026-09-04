"""Command-line interface for the eqanun client.

Runs on stock Python 3.9 (no dependencies). Examples:

    python3 -m eqanun search "mülki məcəllə" --scope title --status in_force -n 10
    python3 -m eqanun count "əmək məcəlləsi"
    python3 -m eqanun get 46944
    python3 -m eqanun fulltext 46944 --out civil-code.txt
    python3 -m eqanun types
    python3 -m eqanun sections
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import EqanunClient, EqanunError
from .mcp_server import force_utf8_stdio


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    # Azerbaijani text is unencodable in the Windows ANSI codepages; without this
    # every command that prints act text dies with UnicodeEncodeError.
    force_utf8_stdio()
    p = argparse.ArgumentParser(prog="eqanun", description="Azerbaijan legislation (e-qanun.az) client")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="search acts by word")
    sp.add_argument("query")
    sp.add_argument("--scope", choices=["title", "text"], default="title")
    sp.add_argument("--status", choices=["in_force", "cancelled", "all"], default="in_force")
    sp.add_argument("--exact", action="store_true")
    sp.add_argument(
        "--types",
        help="comma-separated act-type ids from `types` (e.g. 73 = Constitutional "
             "Court decisions, 87 = Supreme Court Plenum). Omit for no filter.",
    )
    sp.add_argument("--start", type=int, default=0)
    sp.add_argument("-n", "--length", type=int, default=20)

    cp = sub.add_parser("count", help="count acts matching a word")
    cp.add_argument("query")
    cp.add_argument("--scope", choices=["title", "text"], default="title")
    cp.add_argument("--status", choices=["in_force", "cancelled", "all"], default="in_force")
    cp.add_argument("--exact", action="store_true")

    gp = sub.add_parser("get", help="get act metadata by id")
    gp.add_argument("id", type=int)

    fp = sub.add_parser("fulltext", help="get act full text by id")
    fp.add_argument("id", type=int)
    fp.add_argument("--html", action="store_true", help="raw HTML instead of text")
    fp.add_argument("--out", help="write to file instead of stdout")

    sub.add_parser("types", help="list act-type taxonomy")
    sub.add_parser("sections", help="list top-level sections")

    args = p.parse_args(argv)
    c = EqanunClient()

    try:
        if args.cmd == "search":
            types = None
            if args.types:
                try:
                    types = [int(t) for t in args.types.split(",") if t.strip()]
                except ValueError:
                    p.error("--types must be comma-separated integers, e.g. --types 73,87")
            res = c.search(
                args.query, scope=args.scope, status=args.status,
                exact=args.exact, types=types,
                start=args.start, length=args.length,
            )
            _print_json(res)
        elif args.cmd == "count":
            print(c.count(args.query, scope=args.scope, status=args.status, exact=args.exact))
        elif args.cmd == "get":
            meta = c.get_act(args.id)
            meta.pop("_raw", None)
            _print_json(meta)
        elif args.cmd == "fulltext":
            out = c.get_act_fulltext(args.id, as_text=not args.html)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(out)
                print(f"wrote {len(out):,} chars to {args.out}", file=sys.stderr)
            else:
                print(out)
        elif args.cmd == "types":
            _print_json(c.list_types())
        elif args.cmd == "sections":
            _print_json(c.list_sections())
    except EqanunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
