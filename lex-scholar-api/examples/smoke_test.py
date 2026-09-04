#!/usr/bin/env python3
"""Live smoke test for the lexscholar federation (no third-party deps).

Exercises the whole chain against the real upstreams:

    router -> federated search -> dedupe/rank -> per-source adapters
    -> multi-jurisdiction comparison -> full text -> DOI resolution
    -> capability cards

Run:  python3 examples/smoke_test.py
"""

import os
import sys
import time

# Legal scholarship is multilingual; a legacy Windows console codepage would
# otherwise crash on Portuguese/Azerbaijani output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lexscholar import LexScholarClient, route  # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print("   %s %s%s" % ("OK  " if condition else "FAIL", label,
                          (" - " + detail) if detail else ""))


def main() -> int:
    c = LexScholarClient(timeout=30)

    print("1) router picks sources without touching the network")
    cases = [
        ("force majeure in oil and gas", {}, "lawreviewcommons"),
        ("arbitraje de inversiones", {}, "dialnet"),
        ("droit de l'energie", {}, "hal"),
        ("mineracao", {"jurisdiction": "BR"}, "scielo"),
        ("oil law", {"jurisdiction": "AZ"}, "openalex"),
        ("tahkim", {"jurisdiction": "TR"}, "dergipark"),
    ]
    for q, kw, expected in cases:
        plan = route(q, **kw)
        check("route(%r) includes %s" % (q[:26], expected),
              expected in plan["sources"], str(plan["sources"]))
    plan = route("10.1093/jiel/jgaa002")
    check("a DOI short-circuits to resolvers", plan["sources"] == ["crossref", "unpaywall"])

    print("\n2) federated search (auto routing)")
    t = time.time()
    res = c.search("energy arbitration", limit=5)
    check("returned results", res["returned"] > 0, "%d hits" % res["returned"])
    check("reports which sources ran", bool(res["sources_queried"]), str(res["sources_queried"]))
    check("reports what it skipped and why", bool(res["sources_skipped"]))
    check("finished promptly", res["elapsed_seconds"] < 30, "%ss" % res["elapsed_seconds"])
    for r in res["results"][:3]:
        print("      - [%s] %s | %s" % (r["source"], r.get("year"), (r.get("title") or "")[:56]))

    print("\n3) DOAJ peer-review guarantee")
    res = c.search("stabilization clause", peer_reviewed_only=True, limit=5)
    check("all hits marked peer reviewed",
          all(r.get("peer_reviewed") is True for r in res["results"]),
          "%d hits" % res["returned"])
    check("student-edited law reviews excluded",
          any(s["source"] == "lawreviewcommons" for s in res["sources_skipped"]))

    print("\n4) normalized record shape is uniform across sources")
    res = c.search("contract law", source="doaj,openaire", limit=4)
    required = ("source", "title", "citation", "peer_reviewed", "landing_url")
    check("every record carries the contract fields",
          all(all(k in r for k in required) for r in res["results"]),
          "%d records" % res["returned"])
    if res["results"]:
        print("      citation: %s" % res["results"][0]["citation"][:88])

    print("\n5) multi-jurisdiction comparison")
    cmp_res = c.compare_jurisdictions("arbitration", ["FR", "BR", "ID"], limit_per=2)
    got = cmp_res["jurisdictions_with_results"]
    check("at least two jurisdictions answered", len(got) >= 2, str(got))
    check("each jurisdiction routed to its own index",
          len({tuple(g["sources_queried"]) for g in cmp_res["groups"].values()}) > 1,
          str({k: v["sources_queried"] for k, v in cmp_res["groups"].items()}))

    print("\n6) full text (SciELO serves real bodies)")
    sci = c.search("direito", jurisdiction="BR", source="scielo", limit=1)
    if sci["results"]:
        rec = sci["results"][0]
        ft = c.get_fulltext("scielo", rec["id"], collection=rec.get("_scielo_collection", "scl"),
                            max_chars=400)
        # Must be real body text, not a note or a fallback abstract, or the
        # headline capability is silently broken.
        check("returned substantial body text", ft["total_chars"] > 2000,
              "%d chars%s" % (ft["total_chars"], " | " + (ft.get("note") or "")))
        check("paginates full text", ft.get("next_offset") is not None,
              "next_offset=%s" % ft.get("next_offset"))
        if ft["text"]:
            print("      preview: %s..." % ft["text"][:100].replace("\n", " "))
    else:
        check("scielo returned a record to read", False, "no scielo hit to test")

    print("\n7) DOI resolution")
    doi = c.resolve_doi("10.1093/jiel/jgaa002")
    check("resolved the DOI", bool(doi.get("result")),
          (doi.get("result") or {}).get("title", "")[:56])

    print("\n8) capability cards")
    cards = c.list_sources()
    check("ten sources registered", len(cards) == 10, "%d" % len(cards))
    check("openalex reports its metered budget",
          any(s["name"] == "openalex" and "budget" in s for s in cards))

    print("\n%s  passed=%d failed=%d" % ("RESULT:", len(PASS), len(FAIL)))
    if FAIL:
        print("failed: %s" % ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
