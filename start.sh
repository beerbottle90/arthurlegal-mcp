#!/bin/sh
# Start de-eli (the one backend that cannot be imported), then the aggregator.
#
# The aggregator is what answers; de-eli is a local dependency of it. If de-eli
# fails, the aggregator still serves the other nine jurisdictions and reports the
# failure through `status` -- a missing jurisdiction is announced, never silently
# served as an empty result.
set -e

if command -v de-eli-mcp >/dev/null 2>&1; then
    echo "de-eli backend starting on 127.0.0.1:8790" >&2
    TRANSPORT=http PORT=8790 de-eli-mcp >/tmp/de-eli.log 2>&1 &
    # Give it a moment to bind before the aggregator probes it.
    sleep 4
else
    echo "de-eli-mcp not installed; German law will be unavailable" >&2
    export DE_ELI_URL=off
fi

APP="$(cd "$(dirname "$0")" && pwd)"
INDEX_SRC="${INDEX_SOURCE:-/data/index}"
JURISDICTIONS="arthur-tr-hukuk-mcp nl-rechtspraak-mcp pl-sejm-mcp es-boe-mcp ie-statutebook-mcp fi-finlex-mcp"

# Seed the indexes from an attached storage bucket when one is mounted.
#
# The databases are COPIED to local disk rather than opened where they lie. A
# bucket is object storage behind a FUSE mount: SQLite would issue a byte-range
# read for every btree page it touches, and would rely on file locking that
# object storage does not honestly provide. The copy costs seconds at boot; the
# alternative costs a corrupted index that still answers queries.
seeded=0
expected=0
for j in $JURISDICTIONS; do
    expected=$((expected + 1))
    src="$INDEX_SRC/$j.db"
    # arthur-tr-hukuk-mcp 23.09.2026'ya kadar ArthurLegalTR'ydi; baked/ indeksi o adla durabilir.
    if [ ! -f "$src" ] && [ "$j" = "arthur-tr-hukuk-mcp" ] && [ -f "$INDEX_SRC/ArthurLegalTR.db" ]; then
        src="$INDEX_SRC/ArthurLegalTR.db"
    fi
    dst="$APP/$j/data/index.db"
    if [ -f "$dst" ]; then
        seeded=$((seeded + 1))
    elif [ -f "$src" ]; then
        mkdir -p "$APP/$j/data"
        if cp "$src" "$dst"; then
            seeded=$((seeded + 1))
        else
            echo "could not seed $j from $src" >&2
        fi
    fi
done
echo "indexes present: $seeded/$expected (source: $INDEX_SRC)" >&2
if [ "$seeded" -eq "$expected" ]; then
    touch "$APP/.crawled"
fi

# Crawl whatever the bucket did not supply, in the background, and vectorise it
# in the same pass (--embed). Without --embed the index would build but hold no
# vectors: semantic search would report itself "on", contribute nothing, and the
# results would quietly be keyword-only. Blocking on this would fail the
# platform health check; every search tool reports an empty index plainly in the
# meantime.
if [ ! -f "$APP/.crawled" ]; then
    (
      cd "$APP/es-boe-mcp"         && python crawl.py --max 0 --embed                         || true
      cd "$APP/ie-statutebook-mcp" && python crawl.py --from 2015 --to 2026 --embed           || true
      cd "$APP/fi-finlex-mcp"      && python crawl.py --from 2020 --to 2026 --embed           || true
      cd "$APP/pl-sejm-mcp"        && python crawl.py --from 2015 --to 2026 --embed           || true
      cd "$APP/nl-rechtspraak-mcp" && python crawl.py --from 2024-01-01 --to 2026-12-31 --embed || true
      touch "$APP/.crawled"
      echo "index crawl finished" >&2
    ) &
fi

# The Turkish index is baked with its documents but vectorised on the machine:
# the workstation that crawls it has no Voyage key, so vectors for the
# configured model are filled here, in the background, with the platform
# secret. `embed_missing` is idempotent -- a machine that already has them
# does nothing -- and tr_semantik_ara reports semantic: off until it is done.
if [ -n "$EMBEDDINGS_API_KEY" ] && [ -f "$APP/arthur-tr-hukuk-mcp/data/index.db" ]; then
    (cd "$APP/arthur-tr-hukuk-mcp" && python crawl.py --embed-only >/tmp/tr-embed.log 2>&1         && echo "arthur-tr-hukuk-mcp vectors ready" >&2 || echo "arthur-tr-hukuk-mcp embedding failed (see /tmp/tr-embed.log)" >&2) &
fi

cd "$APP"

exec python server.py
