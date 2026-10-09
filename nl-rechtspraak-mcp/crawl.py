"""Build the case-law search index for nl-rechtspraak-mcp.

    python crawl.py --from 2024-01-01 --to 2026-08-30            # summaries (fast)
    python crawl.py --from 2026-01-01 --to 2026-08-30 --full     # + full judgment texts
    python crawl.py --from 2026-06-01 --to 2026-08-30 --full --embed
    python crawl.py --backfill-court      # repair an older index, no network

Two depths, because the cost difference is three orders of magnitude
-------------------------------------------------------------------
**Summary mode (default).** One request per calendar day returns up to 1,000
decisions with their 400-character summaries. A whole year costs ~365 requests.
Broad coverage, shallow text.

**Full mode (``--full``).** Each decision's judgment body needs its own
``content?id=`` request — roughly 13,000 characters each. A single January costs
13,423 requests. Deep text, narrow slice.

The sensible pattern is both: summaries across many years for recall, full text
across the recent slice you actually litigate against. ``server_status`` reports
which days were crawled at which depth, and every search response carries the
index size, so a thin result is never mistaken for a settled question.

Rechtspraak publishes metadata for far more decisions than it publishes texts, so
some documents will legitimately have an empty body even in full mode.

Summary crawls before 2026-10 stored no court, which leaves the ``court``
filter of ``search_caselaw`` unable to match those rows. Every stored title
names the court, so ``--backfill-court`` repairs such an index in place,
offline, instead of crawling it again.
"""

from __future__ import annotations

import argparse
import sys
import time

from rechtspraak import NlError, RechtspraakClient, court_from_title
from retrieval import Index, embeddings_available


def crawl(index: Index, date_from: str, date_to: str, full: bool = False,
          subject: str = "", pause: float = 0.2, max_docs: int = 0) -> int:
    client = RechtspraakClient()
    total = 0
    for day in client.iter_days(date_from, date_to):
        try:
            page = client.search(max_results=1000, date=day, subject=subject)
        except NlError as exc:
            sys.stderr.write("%s skipped: %s\n" % (day, exc))
            continue
        for item in page["results"]:
            ecli = item["ecli"]
            if not ecli:
                continue
            body = item.get("summary") or ""
            court, date_str, subject_str, docket = item.get("court", ""), day, subject, ""
            if full:
                try:
                    doc = client.get_decision(ecli, max_chars=200000)
                    body = doc.get("text") or doc.get("abstract") or body
                    court = doc.get("court") or court
                    date_str = doc.get("date") or day
                    subject_str = doc.get("subject", "")
                    docket = doc.get("docket", "")
                except NlError:
                    pass  # metadata-only decision; keep the summary we already have
                time.sleep(pause)
            index.upsert({
                "ref": ecli,
                "title": item.get("title", ""),
                "body": body,
                "url": item.get("url", ""),
                "lang": "nl",
                "date": date_str,
                "court": court,
                "subject": subject_str,
                # An ECLI is its own citation — never assembled by the model.
                "citation": ecli,
                "meta": {"docket": docket, "depth": "full" if full else "summary"},
            })
            total += 1
            if max_docs and total >= max_docs:
                break
        index.db.commit()
        sys.stderr.write("%s -> %d/%s (running %d)\n"
                         % (day, page["returned"], page["total_matching_filters"], total))
        if max_docs and total >= max_docs:
            break
        if not full:
            time.sleep(pause)
    index.reindex_fts()
    index.set_state("last_crawl", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    prev = index.get_state("coverage")
    note = "%s..%s (%s)" % (date_from, date_to, "full text" if full else "summaries")
    index.set_state("coverage", (prev + " | " + note) if prev else note)
    return total


def backfill_court(index: Index) -> int:
    """Fill the court of rows stored without one, from their titles. No network.

    Returns the number of rows repaired. The FTS tables do not index the court,
    so no rebuild is needed afterwards.
    """
    rows = index.db.execute("SELECT id, title FROM docs WHERE court = ''").fetchall()
    fixed = 0
    for row in rows:
        court = court_from_title(row["title"])
        if court:
            index.db.execute("UPDATE docs SET court = ? WHERE id = ?", (court, row["id"]))
            fixed += 1
    index.db.commit()
    return fixed


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the nl-rechtspraak-mcp case-law index")
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    ap.add_argument("--full", action="store_true", help="fetch full judgment texts (slow)")
    ap.add_argument("--subject", default="", help="rechtsgebied URI, see list_vocabulary")
    ap.add_argument("--max", dest="max_docs", type=int, default=0)
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--index", default=None)
    ap.add_argument("--backfill-court", action="store_true",
                    help="only fill missing courts from stored titles (offline), then exit")
    args = ap.parse_args()

    index = Index(args.index)
    if args.backfill_court:
        sys.stderr.write("court filled for %d decisions\n" % backfill_court(index))
        return
    if not (args.date_from and args.date_to):
        ap.error("--from and --to are required unless --backfill-court is given")
    n = crawl(index, args.date_from, args.date_to, full=args.full,
              subject=args.subject, max_docs=args.max_docs)
    sys.stderr.write("indexed %d decisions\n" % n)
    if args.embed:
        if not embeddings_available():
            sys.stderr.write("EMBEDDINGS_URL not set — skipping vectors.\n")
        else:
            sys.stderr.write("%s\n" % index.embed_missing())


if __name__ == "__main__":
    main()
