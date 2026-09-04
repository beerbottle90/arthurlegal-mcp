"""Build the local search index for pl-sejm-mcp.

    python crawl.py --from 2000 --to 2026        # Dz.U. for those years
    python crawl.py --from 2015 --to 2026 --publisher MP
    python crawl.py --from 2020 --to 2026 --embed

Why an index at all, when the Sejm API is good
----------------------------------------------
``/eli/acts/search`` matches **titles only**. That is fine for "find the Energy
Law" and useless for "which act mentions capacity-market obligations", because
the phrase lives in an article, not a title.

This crawler indexes each act's title, type, issuing body and — the useful part —
its ``keywordsNames``, the Sejm's own controlled subject vocabulary. Combined
with the shared hybrid retrieval that gives real subject search without pulling
down every act's full text.

Bodies are not indexed: most acts are PDF-only (``textHTML: false``), so there is
no text to index for them and a body index would be silently lopsided. Stated in
every search response rather than left for the caller to discover.
"""

from __future__ import annotations

import argparse
import sys
import time

from retrieval import Index, embeddings_available
from sejm import PUBLISHERS, SejmClient, SejmError


def crawl(index: Index, publisher: str, year_from: int, year_to: int,
          pause: float = 0.25) -> int:
    client = SejmClient()
    total = 0
    for year in range(int(year_from), int(year_to) + 1):
        try:
            data = client.list_year(publisher, year, limit=500)
        except SejmError as exc:
            sys.stderr.write("%s %d skipped: %s\n" % (publisher, year, exc))
            continue
        # list_year returns one page; walk the rest by offset.
        got, offset = data["results"], 0
        while len(got) < data["count"]:
            offset += 500
            more = client.list_year(publisher, year, limit=500, offset=offset)
            if not more["results"]:
                break
            got.extend(more["results"])
        for act in got:
            keywords = ", ".join(act.get("keywords") or [])
            index.upsert({
                "ref": act["address"],
                "title": act["title"],
                "body": "\n".join(x for x in (
                    act.get("type"), act.get("released_by"), keywords,
                    act.get("display_address"),
                ) if x),
                "url": act["url"],
                "lang": "pl",
                "date": act.get("announcement_date") or act.get("promulgation") or "",
                "status": act.get("status") or "",
                "court": act.get("released_by") or "",
                "subject": keywords[:200],
                "citation": act["citation"],
                "meta": {
                    "publisher": act.get("publisher"), "year": act.get("year"),
                    "pos": act.get("pos"), "in_force": act.get("in_force"),
                    "has_html": act.get("has_html"), "has_pdf": act.get("has_pdf"),
                    "eli": act.get("eli"),
                },
            })
            total += 1
        index.db.commit()
        sys.stderr.write("%s %d -> %d acts (running %d)\n" % (publisher, year, len(got), total))
        time.sleep(pause)
    index.reindex_fts()
    index.set_state("last_crawl", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    index.set_state("coverage", "%s %s-%s" % (publisher, year_from, year_to))
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the pl-sejm-mcp index")
    ap.add_argument("--publisher", default="DU", choices=sorted(PUBLISHERS))
    ap.add_argument("--from", dest="year_from", type=int, default=2015)
    ap.add_argument("--to", dest="year_to", type=int, default=2026)
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--index", default=None)
    args = ap.parse_args()

    index = Index(args.index)
    n = crawl(index, args.publisher, args.year_from, args.year_to)
    sys.stderr.write("indexed %d acts\n" % n)
    if args.embed:
        if not embeddings_available():
            sys.stderr.write("EMBEDDINGS_URL not set — skipping vectors.\n")
        else:
            sys.stderr.write("%s\n" % index.embed_missing())


if __name__ == "__main__":
    main()
