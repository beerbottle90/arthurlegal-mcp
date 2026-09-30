#!/usr/bin/env python3
"""Build the local index that powers ``semantik_ara``.

    python crawl.py --source kvkk,bddk,epdk,btk,rekabet,spk,sigorta_tahkim,istac --embed
    python crawl.py --source sigorta_tahkim --issues 60-66 --embed
    python crawl.py --source spk --years 2024,2025,2026
    python crawl.py --source bddk,rekabet --fetch-text --limit 200
    python crawl.py --embed-only            # vectorise whatever lacks a vector
    python crawl.py --only-new --source rekabet --pages 40   # tazeleme: yalnız yeni ref'ler
    python crawl.py --backfill-text --source bddk,btk        # gövdesi başlıktan ibaret belgelere tam metin
    python crawl.py --pack                  # dağıtım öncesi: gövde sonda, FTS yeniden + optimize, VACUUM

``--only-new`` is the refresh mode. ``upsert`` REPLACES a document's body and
DROPS its vector, so re-crawling a source that was first crawled with
``--fetch-text`` would silently overwrite full texts with listing stubs and
force every vector to be recomputed. In refresh mode a ref that is already
indexed is skipped before anything is downloaded, and its vector survives.

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
import hashlib
import os
import sys
import time
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import retrieval  # noqa: E402
import sources  # noqa: E402


class _Tagged:
    """Index proxy that stamps the source key into ``subject`` on every upsert.

    With ``only_new`` it also answers ``exists(ref)`` / ``exists_prefix(prefix)``
    for adapters, so they can skip a document before downloading it, and it
    refuses to upsert a ref that is already indexed (keeping its vector).
    Without ``only_new`` both answer False and behaviour is unchanged.
    """

    def __init__(self, index: retrieval.Index, key: str, only_new: bool = False) -> None:
        self.index = index
        self.key = key
        self.only_new = only_new
        self.n = 0
        self.skipped = 0

    def exists(self, ref: str) -> bool:
        if not self.only_new:
            return False
        if self.index.has_ref(ref):
            self.skipped += 1
            return True
        return False

    def exists_prefix(self, prefix: str) -> bool:
        return self.only_new and self.index.has_prefix(prefix)

    def upsert(self, doc: Dict[str, Any]) -> int:
        if self.only_new and self.index.has_ref(str(doc.get("ref") or "")):
            self.skipped += 1
            row = self.index.db.execute("SELECT id FROM docs WHERE ref = ?", (doc["ref"],)).fetchone()
            return int(row["id"])
        meta = dict(doc.get("meta") or {})
        if doc.get("subject") and doc["subject"] != self.key:
            meta.setdefault("topic", doc["subject"])
        doc = dict(doc, subject=self.key, meta=meta)
        self.n += 1
        if self.n % 200 == 0:
            self.index.db.commit()
        return self.index.upsert(doc)


# kurum -> indeks satırından ``get`` kimliği. Yalnız listesi metinsiz gelen kurumlar.
_BACKFILL_ID = {
    "bddk": lambda r: r["ref"].split(":", 1)[1],
    "btk": lambda r: r["url"],
    "rekabet": lambda r: r["ref"].split(":", 1)[1],
}
# Gövde bu kadar karakterden az fazlaysa belge "yalnız başlık" sayılır.
_BACKFILL_PAY = 200


def _backfill(idx: "retrieval.Index", keys: List[str], limit: int, log) -> Dict[str, Any]:
    """Listeden indekslenmiş belgelere tam metni sonradan ekler.

    Neden var: BDDK, BTK ve Rekabet ilk taramada ``--fetch-text`` olmadan
    indekslendi; gövdeleri başlığın kendisidir. 2026-09-20'de ölçüldü: 19.498
    belgenin 13.313'ü (%68) böyleydi. ``semantik_ara`` o belgelerde kararın
    METNİNİ değil yalnız BAŞLIĞINI arıyordu — ve BDDK ile BTK'nin canlı araması
    da başlıkta olduğu için, karar metnine dair soru hiçbir yoldan sorulamıyordu.

    Yeniden başlatılabilir: yalnız gövdesi hâlâ başlıktan ibaret satırları seçer.
    Metni değişen belgenin vektörü silinir; ``embed_missing`` yenisini üretir.
    """
    all_sources = sources.load_all()
    ozet: Dict[str, Any] = {}
    for key in keys:
        src = all_sources.get(key)
        if not src or key not in _BACKFILL_ID:
            log("skip %s: backfill desteklenmiyor (geçerli: %s)" % (key, ", ".join(_BACKFILL_ID)))
            continue
        rows = idx.db.execute(
            "SELECT id, ref, url, title FROM docs WHERE subject = ? "
            "AND length(body) <= length(title) + ? ORDER BY date DESC",
            (key, _BACKFILL_PAY)).fetchall()
        if limit:
            rows = rows[:limit]
        done = bos = 0
        t0 = time.time()
        for i, r in enumerate(rows, 1):
            try:
                g = src.get({"id": _BACKFILL_ID[key](r), "page_chars": 200000})
            except Exception as exc:  # noqa: BLE001 - tek belge taramayı durdurmasın
                g = {"error": "%s: %s" % (type(exc).__name__, exc)}
            text = (g.get("text") or "").strip()
            if len(text) <= len(r["title"] or "") + _BACKFILL_PAY:
                bos += 1                        # taranmış PDF / erişilemedi: başlıkta kalır, sonra yeniden denenir
            else:
                idx.db.execute("UPDATE docs SET body = ? WHERE id = ?", (text[:200000], r["id"]))
                idx.db.execute("DELETE FROM vecs WHERE doc_id = ?", (r["id"],))
                done += 1
            if i % 100 == 0:
                idx.db.commit()
                log("%s: %d/%d (metin %d, boş %d, %.0fs)" % (key, i, len(rows), done, bos, time.time() - t0))
        idx.db.commit()
        ozet[key] = {"aday": len(rows), "metin_eklendi": done, "metin_yok": bos}
        log("%s: backfill %s in %.0fs" % (key, ozet[key], time.time() - t0))
    return ozet


# Paketlenmiş dizinde süzgeç sütunları satırın başında, uzun gövde en sonda durur.
_PACK_ONCE = ("id", "subject", "date", "court", "lang", "status")


def _parmak_izi(db, cols: List[str]) -> tuple:
    h = hashlib.sha256()
    n = 0
    for r in db.execute("SELECT %s FROM docs ORDER BY id" % ", ".join(sorted(cols))):
        h.update(repr(tuple(r)).encode("utf-8"))
        n += 1
    return n, h.hexdigest()


def _pack(idx: "retrieval.Index", log) -> Dict[str, Any]:
    """Dağıtım öncesi paketleme: ``docs`` gövde sonda yeniden kurulur, FTS baştan kurulup birleştirilir, VACUUM.

    Neden var: SQLite satırı sütun sırasıyla saklar. Şemada ``body`` ortada; ondan sonra gelen ``date``,
    ``subject`` ve ``court``'a ulaşmak için taşma sayfası zinciri baştan sona okunur, yani kurum süzgeçli
    her aramada her aday belgenin bütün metni diskten geçer. Rekabet tam metni gelince (30.09.2026, ~270 MB
    metin) kurum süzgeçli aramada okunan veri ve ``status`` süresi katlandı; gövde sonda iken eski düzeyin
    altına indi. Kod sütunları hep ADIYLA kullanır, FTS5 dış içerik tablosu da; sıra hiçbir sonucu
    değiştirmez. Satırlar, id'ler ve vektörler aynen kalır; önce/sonra parmak iziyle denetlenir.
    """
    db = idx.db
    db.commit()
    if db.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("foreign_keys açık: DROP TABLE docs vektörleri CASCADE ile silerdi")
    info = {r[1]: r for r in db.execute("PRAGMA table_info(docs)")}
    cols = list(info)
    sira = [c for c in _PACK_ONCE if c in info] + [c for c in cols if c not in _PACK_ONCE and c != "body"] + ["body"]
    yeniden = cols != sira
    if yeniden:
        tekil = set()
        for ix in db.execute("PRAGMA index_list(docs)").fetchall():
            if ix[2] and ix[3] == "u":
                tekil.update(r[2] for r in db.execute("PRAGMA index_info(%s)" % ix[1]))
        dizinler = [r[0] for r in db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'docs' AND sql IS NOT NULL")]

        def tanim(c: str) -> str:
            _, ad, tur, notnull, varsayilan, pk = info[c]
            s = "%s %s" % (ad, tur)
            if pk:
                return s + " PRIMARY KEY"
            s += " UNIQUE" if ad in tekil else ""
            s += " NOT NULL" if notnull else ""
            return s + (" DEFAULT %s" % varsayilan if varsayilan is not None else "")

        once = _parmak_izi(db, cols)
        adlar = ", ".join(sira)
        db.execute("BEGIN")
        db.execute("CREATE TABLE docs__pack (%s)" % ", ".join(tanim(c) for c in sira))
        db.execute("INSERT INTO docs__pack(%s) SELECT %s FROM docs ORDER BY id" % (adlar, adlar))
        db.execute("DROP TABLE docs")
        db.execute("ALTER TABLE docs__pack RENAME TO docs")
        for sql in dizinler:
            db.execute(sql)
        db.execute("COMMIT")
        if _parmak_izi(db, cols) != once:
            raise RuntimeError("paketleme içeriği değiştirdi")
        log("pack: docs gövde sonda yeniden kuruldu (%d belge, içerik aynı)" % once[0])
    t0 = time.time()
    idx.reindex_fts()
    for t in ("docs_fts", "docs_tri"):
        db.execute("INSERT INTO %s(%s) VALUES('optimize')" % (t, t))
    db.commit()
    db.execute("VACUUM")
    butunluk = db.execute("PRAGMA integrity_check").fetchone()[0]
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if butunluk != "ok":
        raise RuntimeError("integrity_check: %s" % butunluk)
    ozet = {"yeniden_kuruldu": yeniden, "belge": idx.count(), "vektor": db.execute(
        "SELECT COUNT(*) FROM vecs").fetchone()[0], "boyut_mb": round(os.path.getsize(idx.path) / 1e6)}
    log("pack: FTS yeniden kuruldu + optimize, VACUUM, integrity ok (%.0fs) %s" % (time.time() - t0, ozet))
    return ozet


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
    ap.add_argument("--only-new", action="store_true",
                    help="refresh mode: skip refs already indexed (keeps their text and vector)")
    ap.add_argument("--fetch-text", action="store_true", help="download full decision texts (slow) where supported")
    ap.add_argument("--backfill-text", action="store_true",
                    help="no crawl: fetch full text for documents indexed from a listing only "
                         "(bddk, btk, rekabet); resumable; --limit caps documents per source")
    ap.add_argument("--pack", action="store_true",
                    help="no crawl: pack the index for shipping (body column last, FTS rebuilt + optimized, "
                         "VACUUM); content, ids and vectors unchanged")
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

    if args.pack:
        _pack(idx, log)
        return 0
    if args.backfill_text:
        keys = [k.strip() for k in args.source.split(",") if k.strip()] or ["bddk", "btk"]
        _backfill(idx, keys, args.limit, log)
        idx.reindex_fts()
        idx.set_state("last_backfill", time.strftime("%Y-%m-%dT%H:%M:%S"))
        log("index: %d documents" % idx.count())
    elif not args.embed_only:
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
            tagged = _Tagged(idx, key, only_new=args.only_new)
            try:
                res = src.crawl(tagged, **opts)
            except Exception as exc:  # noqa: BLE001
                log("%s: crawl failed: %s: %s" % (key, type(exc).__name__, exc))
                continue
            idx.db.commit()
            log("%s: %s in %.0fs%s" % (key, res, time.time() - t0,
                                       (", %d already indexed, skipped" % tagged.skipped) if args.only_new else ""))
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
