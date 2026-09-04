#!/usr/bin/env python3
"""Live smoke test for the eqanun client (no third-party deps).

Exercises the full chain against the real API:
    search -> count -> get_act -> get_act_fulltext -> list_types/list_sections

Run:  python3 examples/smoke_test.py
"""

import os
import sys

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eqanun import EqanunClient  # noqa: E402
from eqanun.mcp_server import force_utf8_stdio  # noqa: E402


def main() -> int:
    # Act titles are Azerbaijani; the Windows ANSI codepages cannot encode them.
    force_utf8_stdio()
    c = EqanunClient()
    ok = True

    print("1) search(scope=title, status=in_force) for 'mülki məcəllə'")
    hits = c.search("mülki məcəllə", scope="title", status="in_force", length=5)
    print(f"   total={hits['total']}  returned={len(hits['results'])}")
    if not hits["results"]:
        print("   !! expected at least one result")
        return 1
    for r in hits["results"][:5]:
        print(f"   - [{r['id']}] {r.get('statusName')} | {r['title'][:70]}")

    top = hits["results"][0]

    print("\n2) count() vs search().total (should match)")
    n = c.count("mülki məcəllə", scope="title", status="in_force")
    print(f"   count={n}  search.total={hits['total']}  match={n == hits['total']}")
    ok = ok and (n == hits["total"])

    print(f"\n3) get_act({top['id']})")
    act = c.get_act(top["id"])
    print(f"   title      : {act['title'][:70]}")
    print(f"   type/status: {act['typeName']} / {act['statusName']}")
    print(f"   acceptDate : {act['acceptDate']}   htmlUrl: {act['htmlUrl']}")
    ok = ok and bool(act["htmlUrl"])

    print(f"\n4) get_act_fulltext({top['id']}) -> plain text")
    text = c.get_act_fulltext(top["id"])
    print(f"   chars={len(text):,}")
    preview = text[:240].replace("\n", " ")
    print(f"   preview: {preview}...")
    ok = ok and len(text) > 200

    print("\n5) list_sections()")
    secs = c.list_sections()
    print(f"   {len(secs)} sections: {[s.get('name','').strip() for s in secs]}")
    ok = ok and len(secs) >= 1

    print("\n6) list_types() (first 3)")
    types = c.list_types()
    print(f"   {len(types)} types total")
    for t in types[:3]:
        print(f"   - [{t.get('id')}] {t.get('name')}")
    ok = ok and len(types) >= 1

    print("\nRESULT:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
