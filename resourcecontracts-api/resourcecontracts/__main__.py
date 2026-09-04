"""Command-line interface for the resourcecontracts client.

Runs on stock Python 3.9 (no dependencies). Examples:

    python3 -m resourcecontracts search --country az --resource Hydrocarbons -n 10
    python3 -m resourcecontracts search "gas" --country az
    python3 -m resourcecontracts count --country az
    python3 -m resourcecontracts meta 5158
    python3 -m resourcecontracts text 5158 --out absheron.txt
    python3 -m resourcecontracts annotations 5158
    python3 -m resourcecontracts countries
    python3 -m resourcecontracts resources
    python3 -m resourcecontracts years
    python3 -m resourcecontracts categories
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import ResourceContractsClient, ResourceContractsError


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _add_filters(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("query", nargs="?", default="", help="free-text query (optional)")
    sp.add_argument("--country", help="ISO alpha-2 code, e.g. az")
    sp.add_argument("--resource", help="e.g. Hydrocarbons, Gold")
    sp.add_argument("--year", type=int)
    sp.add_argument("--contract-type", dest="contract_type")
    sp.add_argument("--document-type", dest="document_type")
    sp.add_argument("--language")
    sp.add_argument("--company", dest="company_name")
    sp.add_argument("--annotation-category", dest="annotation_category")
    sp.add_argument("--annotated", action="store_true")


def _filter_kwargs(args) -> dict:
    kw = dict(
        country=args.country, resource=args.resource, year=args.year,
        contract_type=args.contract_type, document_type=args.document_type,
        language=args.language, company_name=args.company_name,
        annotation_category=args.annotation_category,
    )
    if getattr(args, "annotated", False):
        kw["annotated"] = True
    return kw


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="resourcecontracts",
        description="ResourceContracts.org petroleum & mining contracts client",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="search contracts")
    _add_filters(sp)
    sp.add_argument("--from", dest="from_", type=int, default=0)
    sp.add_argument("-n", "--per-page", dest="per_page", type=int, default=20)

    cp = sub.add_parser("count", help="count contracts matching filters")
    _add_filters(cp)

    sub.add_parser("total", help="grand total of all published contracts")

    mp = sub.add_parser("meta", help="get contract metadata by id")
    mp.add_argument("id", type=int)

    tp = sub.add_parser("text", help="get contract full text by id (cleaned)")
    tp.add_argument("id", type=int)
    tp.add_argument("--html", action="store_true", help="raw HTML instead of text")
    tp.add_argument("--out", help="write to file instead of stdout")

    ap = sub.add_parser("annotations", help="get contract annotations by id")
    ap.add_argument("id", type=int)
    ap.add_argument("--page", type=int, default=None,
                    help="only annotations on this PDF page (default: all)")

    sub.add_parser("countries", help="list countries with counts")
    sub.add_parser("resources", help="list resources with counts")
    sub.add_parser("years", help="list years with counts")
    sub.add_parser("categories", help="list annotation categories")

    args = p.parse_args(argv)
    c = ResourceContractsClient()

    try:
        if args.cmd == "search":
            _print_json(c.search(
                args.query, from_=args.from_, per_page=args.per_page,
                **_filter_kwargs(args),
            ))
        elif args.cmd == "count":
            print(c.count(args.query, **_filter_kwargs(args)))
        elif args.cmd == "total":
            print(c.total_count())
        elif args.cmd == "meta":
            _print_json(c.get_metadata(args.id))
        elif args.cmd == "text":
            out = c.get_fulltext(args.id, as_text=not args.html)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(out)
                print(f"wrote {len(out):,} chars to {args.out}", file=sys.stderr)
            else:
                print(out)
        elif args.cmd == "annotations":
            _print_json(c.get_annotations(args.id, page=args.page))
        elif args.cmd == "countries":
            _print_json(c.list_countries())
        elif args.cmd == "resources":
            _print_json(c.list_resources())
        elif args.cmd == "years":
            _print_json(c.list_years())
        elif args.cmd == "categories":
            _print_json(c.list_annotation_categories())
    except ResourceContractsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
