#!/bin/sh
# Serve. Never crawl at boot: the index ships with the image (data/index.db).
# If it is missing, the server still answers every live source and `status`
# says the local index is empty — an honest state, not a broken one.
set -e
APP="$(cd "$(dirname "$0")" && pwd)"
cd "$APP"
if [ -f "$APP/data/index.db" ]; then
    echo "local index present: $(du -h "$APP/data/index.db" | cut -f1)" >&2
else
    echo "local index absent — semantik_ara will report an empty index" >&2
fi
exec python server.py --transport "${MCP_TRANSPORT:-http}" --host "${MCP_HOST:-0.0.0.0}" --port "${MCP_PORT:-8080}"
