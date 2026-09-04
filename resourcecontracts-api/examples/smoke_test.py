#!/usr/bin/env python3
"""Live smoke test for the resourcecontracts client (no third-party deps).

Exercises the full chain against the real API:
    search -> count -> get_metadata -> get_fulltext -> get_annotations
    -> list_countries / list_resources / list_annotation_categories

Run:  python3 examples/smoke_test.py
"""

import os
import sys

# Contracts carry non-Latin text (e.g. Azerbaijani 'Ə'); force UTF-8 stdout so
# previews don't crash on a legacy Windows console codepage (cp1254/cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resourcecontracts import ResourceContractsClient  # noqa: E402


def main() -> int:
    c = ResourceContractsClient()
    ok = True

    print("1) search(country='az', resource='Hydrocarbons')")
    hits = c.search(country="az", resource="Hydrocarbons", per_page=5)
    print(f"   total={hits['total']}  returned={len(hits['results'])}")
    print(f"   facets: resource={hits['facets']['resource']} years={hits['facets']['year']}")
    if not hits["results"]:
        print("   !! expected at least one result")
        return 1
    for r in hits["results"][:5]:
        print(f"   - [{r['id']}] {r.get('year_signed')} | {r['name'][:64]}")

    top = hits["results"][0]

    print("\n2) count(country='az') vs search(country='az').total")
    n = c.count(country="az")
    allaz = c.search(country="az", per_page=1)["total"]
    print(f"   count={n}  search.total={allaz}  match={n == allaz}")
    ok = ok and (n == allaz)

    print(f"\n3) get_metadata({top['id']})")
    meta = c.get_metadata(top["id"])
    print(f"   name       : {meta.get('name','')[:64]}")
    print(f"   resource   : {meta.get('resource')}  pages: {meta.get('number_of_pages')}")
    print(f"   type       : {meta.get('contract_type')}")
    ok = ok and bool(meta.get("name"))

    print(f"\n4) get_fulltext({top['id']}, pages 1-3) -> clean plain text")
    pages = c.get_page_count(top["id"])
    text = c.get_fulltext(top["id"], start_page=1, page_count=3)
    print(f"   total_pages={pages}  chars(pp.1-3)={len(text):,}")
    preview = text[:200].replace("\n", " ")
    print(f"   preview: {preview}...")
    ok = ok and len(text) > 100

    print(f"\n5) get_annotations({top['id']}) -> curated key clauses")
    ann = c.get_annotations(top["id"])
    result = ann.get("result") or []
    print(f"   total={ann.get('total')}  returned={len(result)}")
    for a in result[:4]:
        print(f"   - {a.get('category')} @ {a.get('article_reference')}: {str(a.get('text'))[:48]}")
    ok = ok and (ann.get("total") is not None)

    print("\n6) list_resources() (top 5 by count)")
    res = c.list_resources()
    print(f"   {len(res)} resources total")
    for r in res[:5]:
        print(f"   - {r.get('resource')}: {r.get('contract')}")
    ok = ok and len(res) >= 1

    print("\n7) list_annotation_categories() (first 5)")
    cats = c.list_annotation_categories()
    print(f"   {len(cats)} categories: {cats[:5]}")
    ok = ok and len(cats) >= 1

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
