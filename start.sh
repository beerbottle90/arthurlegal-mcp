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

# Crawl the indexes in the background when they are missing, and vectorise them
# in the same pass (--embed). Without --embed the index would build but hold no
# vectors: semantic search would report itself "on", contribute nothing, and the
# results would quietly be keyword-only. Blocking on this would fail the
# platform health check; every search tool reports an empty index plainly in the
# meantime.
APP="$(cd "$(dirname "$0")" && pwd)"
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

cd "$APP"

exec python server.py
