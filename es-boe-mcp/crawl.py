"""Build the local search index for es-boe-mcp.

    python crawl.py                    # full metadata crawl (~20 requests)
    python crawl.py --embed            # ...then compute vectors, if EMBEDDINGS_URL is set
    python crawl.py --backfill-text    # add the head of each act's text (one request per act)

What gets indexed, and why only this
------------------------------------
Title, official number, issuing department, rango, dates and ámbito — **not**
the consolidated body text, and **not** ``materias``. BOE exposes its subject
tags only on the per-act detail endpoint, so indexing them would cost one
request per act (12,376 of them) rather than the ~20 this crawl takes. The
subject signal therefore comes from the title, which for Spanish legislation is
descriptive by convention ("Ley 17/2006, de control ambiental integrado").

That is a deliberate trade, not an oversight. A single consolidated act runs to
roughly a million characters (the Ley de Sociedades de Capital is 960,963), so
indexing bodies for the whole corpus would mean tens of gigabytes for a server
whose job is to *find the act*. Spanish legislation is looked up by name,
number and subject; once the act is identified, ``get_act_text`` fetches the
authoritative text on demand.

The consequence is stated in every search response so it cannot be mistaken:
this searches metadata, so a phrase buried in article 348 bis will not be found
by searching for it.

The head of the text is the exception (``--backfill-text``, 2026-10-10): the
first 8,000 characters of the text in force, which are the preamble and the
opening articles, the part that says in plain words what the act regulates. With
titles alone, a question asked in plain words found its act in the top 10 three
times in four (an independent 120-question set, 2026-10-10). The head costs one
request per act, a second apart, and adds ~100 MB of text instead of tens of
gigabytes. It sits after ``TEXT_MARK`` in the body, and a metadata re-crawl keeps it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import boe
from boe import BoeClient, BoeError
from retrieval import Index, embeddings_available

PAGE = 1000  # verified working ceiling for the list endpoint
# Where the metadata lines end and the head of the consolidated text begins.
TEXT_MARK = "\n\n[texto consolidado, inicio]\n"
HEAD_CHARS = 8000


def crawl(index: Index, max_items: int = 0, pause: float = 0.3) -> int:
    client = BoeClient()
    offset, seen = 0, 0
    while True:
        try:
            batch = client.list_consolidated(limit=PAGE, offset=offset)
        except BoeError as exc:
            sys.stderr.write("stopped at offset %d: %s\n" % (offset, exc))
            break
        if not batch:
            break
        for item in batch:
            old = index.db.execute("SELECT body, meta FROM docs WHERE ref = ?", (item["id"],)).fetchone()
            head, text_chars = "", None
            if old is not None and TEXT_MARK in (old["body"] or ""):
                head = TEXT_MARK + old["body"].split(TEXT_MARK, 1)[1]
                text_chars = json.loads(old["meta"] or "{}").get("text_chars")
            index.upsert(
                {
                    "ref": item["id"],
                    "title": item["title"],
                    # The searchable surface: everything that identifies the act.
                    "body": "\n".join(
                        x for x in (
                            item.get("numero_oficial"),
                            item.get("rango"),
                            item.get("departamento"),
                            item.get("ambito"),
                        ) if x
                    ) + head,
                    "url": item["url"],
                    "lang": "es",
                    "date": item.get("date") or item.get("published") or "",
                    "court": item.get("departamento") or "",
                    "subject": item.get("ambito") or "",
                    # BOE's own repeal signal, indexed so search can filter on it.
                    "status": item.get("status") or "",
                    "citation": "%s (%s)" % (item["title"], item["id"]),
                    "meta": {
                        "rango": item.get("rango"),
                        "numero_oficial": item.get("numero_oficial"),
                        "published": item.get("published"),
                        "fecha_vigencia": item.get("fecha_vigencia"),
                        "estado_consolidacion": item.get("estado_consolidacion"),
                        "eli": item.get("eli"),
                        **({"text_chars": text_chars} if text_chars is not None else {}),
                    },
                }
            )
            seen += 1
        index.db.commit()
        sys.stderr.write("indexed %d\r" % seen)
        sys.stderr.flush()
        if max_items and seen >= max_items:
            break
        if len(batch) < PAGE:
            break
        offset += PAGE
        time.sleep(pause)  # be a polite client of a public service
    index.reindex_fts()
    index.set_state("last_crawl", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    index.set_state("corpus", "BOE legislación consolidada (metadata)")
    sys.stderr.write("\nindexed %d documents\n" % seen)
    return seen


def text_head(client: BoeClient, boe_id: str, max_chars: int = HEAD_CHARS) -> str:
    """The first ``max_chars`` of the text in force today, in one request.

    ``BoeClient.get_text`` asks for the metadata first; a crawl over every act
    does not need it, so this reads ``/texto`` alone with the same version rule.
    """
    texto = client._part(boe_id, "texto").find("texto")
    if texto is None:
        return ""
    day = boe._compact_date("")
    blocks, size = [], 0
    for bloque in texto.findall("bloque"):
        version, _ = boe._version_on(bloque, day)
        if version is None:
            continue
        lines = boe._lines(version)
        if lines:
            blocks.append("\n".join(lines))
            size += len(blocks[-1]) + 2
            if size >= max_chars:
                break
    return "\n\n".join(blocks)[:max_chars]


def backfill_text(index: Index, limit: int = 0, pace: float = 1.0,
                  max_chars: int = HEAD_CHARS, log=None) -> dict:
    """Append the head of the consolidated text to every act that lacks one.

    Restartable: an act is done once its meta carries ``text_chars`` (0 when
    BOE has no text for it). A changed body drops its vector; embed afterwards.
    """
    log = log or (lambda m: sys.stderr.write(m + "\n"))
    client = BoeClient()
    rows = [r for r in index.db.execute("SELECT id, ref, body, meta FROM docs ORDER BY id").fetchall()
            if "text_chars" not in json.loads(r["meta"] or "{}")]
    if limit:
        rows = rows[:limit]
    filled = empty = failed = 0
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        if i > 1:
            time.sleep(pace)
        meta = json.loads(r["meta"] or "{}")
        try:
            head = text_head(client, r["ref"], max_chars)
        except BoeError as exc:
            failed += 1
            log("%s: %s" % (r["ref"], exc))
            continue                      # no text_chars: the next run tries again
        meta["text_chars"] = len(head)
        if head:
            body = (r["body"] or "").split(TEXT_MARK, 1)[0] + TEXT_MARK + head
            index.db.execute("UPDATE docs SET body = ?, meta = ? WHERE id = ?",
                             (body, json.dumps(meta, ensure_ascii=False), r["id"]))
            index.db.execute("DELETE FROM vecs WHERE doc_id = ?", (r["id"],))
            filled += 1
        else:
            index.db.execute("UPDATE docs SET meta = ? WHERE id = ?",
                             (json.dumps(meta, ensure_ascii=False), r["id"]))
            empty += 1
        if i % 50 == 0:
            index.db.commit()
            log("%d/%d (text %d, none %d, failed %d, %.0fs)" % (i, len(rows), filled, empty, failed,
                                                                 time.time() - t0))
    index.db.commit()
    index.reindex_fts()
    index.set_state("corpus", "BOE legislación consolidada (metadata + first %d characters of the "
                              "text in force)" % max_chars)
    return {"candidates": len(rows), "text": filled, "no_text": empty, "failed": failed}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the es-boe-mcp index")
    ap.add_argument("--max", type=int, default=0, help="stop after N acts (0 = all)")
    ap.add_argument("--embed", action="store_true", help="compute vectors after crawling")
    ap.add_argument("--index", default=None, help="index path (default $INDEX_PATH or index.db)")
    ap.add_argument("--backfill-text", action="store_true",
                    help="no list crawl: add the head of each act's consolidated text")
    ap.add_argument("--limit", type=int, default=0, help="--backfill-text: at most N acts (0 = all)")
    ap.add_argument("--pace", type=float, default=1.0,
                    help="--backfill-text: seconds between requests to BOE (default 1)")
    args = ap.parse_args()

    index = Index(args.index)
    if args.backfill_text:
        sys.stderr.write("%s\n" % backfill_text(index, limit=args.limit, pace=args.pace))
    else:
        crawl(index, max_items=args.max)
    if args.embed:
        if not embeddings_available():
            sys.stderr.write("EMBEDDINGS_URL not set — skipping vectors.\n")
        else:
            sys.stderr.write("%s\n" % index.embed_missing())


if __name__ == "__main__":
    main()
