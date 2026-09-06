#!/usr/bin/env python3
"""Build the local index that powers ``semantik_ara``.

    python crawl.py --source kvkk,bddk,epdk,btk,rekabet,spk,sigorta_tahkim,istac --embed
    python crawl.py --source sigorta_tahkim --issues 60-66 --embed
    python crawl.py --source spk --years 2024,2025,2026
    python crawl.py --source bddk,rekabet --fetch-text --limit 200
    python crawl.py --embed-only            # vectorise whatever lacks a vector

The index is a single SQLite file (``INDEX_PATH``, default ``data/index.db``):
FTS5 for keywords, a trigram table for typos, and one vector per document when
an embeddings endpoint is configured (``EMBEDDINGS_URL`` / ``EMBEDDINGS_MODEL``
/ ``EMBEDDINGS_API_KEY`` — Voyage, OpenAI-compatible, or a local Ollama).

Crawling is a maintenance task run on a workstation and the file is shipped
with the container (see ``Dockerfile``); a booting server never crawls.

Every document is stored with ``subject = <kurum key>`` so ``semantik_ara``
can filter by regulator; the topic label each adapter chose goes into meta.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import retrieval  # noqa: E402
import sources  # noqa: E402


class _Tagged:
    """Index proxy that stamps the source key into ``subject`` on every upsert."""

    def __init__(self, index: retrieval.Index, key: str) -> None:
        self.index = index
        self.key = key
        self.n = 0

    def upsert(self, doc: Dict[str, Any]) -> int:
        meta = dict(doc.get("meta") or {})
        if doc.get("subject") and doc["subject"] != self.key:
            meta.setdefault("topic", doc["subject"])
        doc = dict(doc, subject=self.key, meta=meta)
        self.n += 1
        if self.n % 200 == 0:
            self.index.db.commit()
        return self.index.upsert(doc)


def _parse_range(spec: str) -> List[int]:
    out: List[int] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="", help="comma-separated source keys; default = every crawlable source")
    ap.add_argument("--embed", action="store_true", help="vectorise new documents after crawling")
    ap.add_argument("--embed-only", action="store_true", help="skip crawling; only fill missing vectors")
    ap.add_argument("--fetch-text", action="store_true", help="download full decision texts (slow) where supported")
    ap.add_argument("--limit", type=int, default=0, help="max documents per source (0 = no limit)")
    ap.add_argument("--pages", type=int, default=0, help="max listing pages per source where paged")
    ap.add_argument("--years", default="", help="spk: bülten years, e.g. 2024,2025,2026")
    ap.add_argument("--issues", default="", help="sigorta_tahkim: journal issues, e.g. 50-66")
    ap.add_argument("--markets", default="", help="epdk: elektrik,dogalgaz,petrol,lpg,denetim")
    ap.add_argument("--index", default=os.environ.get("INDEX_PATH") or os.path.join(HERE, "data", "index.db"))
    args = ap.parse_args(argv)

    os.makedirs(os.path.dirname(args.index), exist_ok=True)
    idx = retrieval.Index(args.index)
    log = lambda m: sys.stderr.write(m + "\n")  # noqa: E731

    if not args.embed_only:
        all_sources = sources.load_all()
        keys = [k.strip() for k in args.source.split(",") if k.strip()] or \
               [k for k, s in all_sources.items() if s.crawlable]
        for key in keys:
            src = all_sources.get(key)
            if not src or not src.crawlable:
                log("skip %s: not crawlable (%s)" % (key, sources.failed().get(key, "")))
                continue
            opts: Dict[str, Any] = {"log": log}
            if args.fetch_text:
                opts["fetch_text"] = True
            if args.limit:
                opts["limit"] = args.limit
            if args.pages:
                opts["max_pages"] = args.pages
            if key == "spk" and args.years:
                opts["years"] = _parse_range(args.years)
            if key == "sigorta_tahkim" and args.issues:
                opts["issues"] = _parse_range(args.issues)
            if key == "epdk" and args.markets:
                opts["markets"] = [m.strip() for m in args.markets.split(",") if m.strip()]
            # Drop options the adapter does not accept.
            import inspect
            accepted = set(inspect.signature(src.crawl).parameters)
            opts = {k: v for k, v in opts.items() if k in accepted or "kwargs" in accepted}
            t0 = time.time()
            tagged = _Tagged(idx, key)
            try:
                res = src.crawl(tagged, **opts)
            except Exception as exc:  # noqa: BLE001
                log("%s: crawl failed: %s: %s" % (key, type(exc).__name__, exc))
                continue
            idx.db.commit()
            log("%s: %s in %.0fs" % (key, res, time.time() - t0))
        idx.reindex_fts()
        idx.set_state("last_crawl", time.strftime("%Y-%m-%dT%H:%M:%S"))
        log("index: %d documents" % idx.count())

    if args.embed or args.embed_only:
        t0 = time.time()
        res = idx.embed_missing()
        log("embed: %s in %.0fs (vectorised %d/%d)" % (res, time.time() - t0, idx.vector_count(), idx.count()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
