"""Build the search index for ie-statutebook-mcp.

    python crawl.py --from 2010 --to 2026
    python crawl.py --from 2020 --to 2026 --embed

The Irish Statute Book has no year-index API — ``/eli/{year}/act/`` renders a
page with no Act links in it — so the crawler walks Act numbers from 1 upward and
stops after a run of consecutive 404s. Acts are numbered sequentially with the
occasional gap, so a single miss is not the end of the year.

Whole Act bodies are indexed. Irish Acts are long but the yearly volume is small
(a few dozen Acts), so unlike Spain this corpus fits comfortably and full-text
search works properly.
"""

from __future__ import annotations

import argparse
import sys
import time

from retrieval import Index, embeddings_available
from statutebook import StatuteBookClient


def crawl(index: Index, year_from: int, year_to: int, probe_limit: int = 80,
          pause: float = 0.2) -> int:
    client = StatuteBookClient()
    total = 0
    for year in range(int(year_from), int(year_to) + 1):
        acts = client.list_year(year, probe_limit=probe_limit)
        for act in acts:
            index.upsert({
                "ref": "IE/%d/act/%d" % (act["year"], act["number"]),
                "title": act["title"],
                "body": act["text"],
                "url": act["url"],
                "lang": "en",
                "date": "%d-01-01" % act["year"],
                "status": "as enacted",
                "court": "Oireachtas",
                "citation": act["citation"],
                "meta": {"year": act["year"], "number": act["number"],
                         "version": "enacted"},
            })
            total += 1
        index.db.commit()
        sys.stderr.write("%d -> %d Acts (running %d)\n" % (year, len(acts), total))
        time.sleep(pause)
    index.reindex_fts()
    index.set_state("last_crawl", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    prev = index.get_state("coverage")
    note = "Acts %s-%s (as enacted)" % (year_from, year_to)
    index.set_state("coverage", (prev + " | " + note) if prev else note)
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the ie-statutebook-mcp index")
    ap.add_argument("--from", dest="year_from", type=int, default=2015)
    ap.add_argument("--to", dest="year_to", type=int, default=2026)
    ap.add_argument("--probe-limit", type=int, default=80,
                    help="highest Act number to probe per year")
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--index", default=None)
    args = ap.parse_args()

    index = Index(args.index)
    n = crawl(index, args.year_from, args.year_to, probe_limit=args.probe_limit)
    sys.stderr.write("indexed %d Acts\n" % n)
    if args.embed:
        if not embeddings_available():
            sys.stderr.write("EMBEDDINGS_URL not set — skipping vectors.\n")
        else:
            sys.stderr.write("%s\n" % index.embed_missing())


if __name__ == "__main__":
    main()
