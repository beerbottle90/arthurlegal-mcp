#!/usr/bin/env bash
#
# Expose the lex-scholar-api MCP server over a public HTTPS URL, the
# fast/cheap way, using a Cloudflare quick tunnel (free, no account, no
# ngrok-style interstitial).
#
# Usage:
#   ./run-public.sh
# then paste the printed  https://<something>.trycloudflare.com/mcp  URL into the
# Copilot Studio MCP connector (mcp-connector.swagger.json -> host), No auth.
#
# Notes on responsiveness / stability:
#   - Cloudflare's edge is fast (no cold start; the origin stays warm while the
#     script runs). Keep the host machine awake.
#   - A quick-tunnel URL CHANGES every run and is "best effort". For a STABLE URL
#     with production reliability (recommended once it works), switch to a named
#     Cloudflare tunnel or Tailscale Funnel — see copilot-studio/RUNBOOK.md.
#
set -euo pipefail

PORT="${LEXSCHOLAR_PORT:-8000}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$HERE/.tools"
mkdir -p "$TOOLS"

command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }

# 1) Bootstrap cloudflared into ./.tools if not already present.
CF="$TOOLS/cloudflared"
if [ ! -x "$CF" ]; then
  OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$(uname -m)" in
    arm64|aarch64) A=arm64 ;;
    *)             A=amd64 ;;
  esac
  echo "Downloading cloudflared ($OS-$A) into .tools/ ..."
  curl -fsSL -o "$TOOLS/cf.tgz" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-$OS-$A.tgz"
  tar -xzf "$TOOLS/cf.tgz" -C "$TOOLS"
  chmod +x "$CF"
  rm -f "$TOOLS/cf.tgz"
fi

# 2) Start the MCP server (background).
echo "Starting MCP server on http://127.0.0.1:$PORT/mcp ..."
python3 "$HERE/server.py" --transport http --host 127.0.0.1 --port "$PORT" &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT

# 3) Open the tunnel in the foreground. cloudflared prints the public URL in a box;
#    append /mcp to it for the connector. Ctrl-C stops both.
echo ""
echo ">>> When the box below shows https://XXXX.trycloudflare.com , your MCP URL is:"
echo ">>>     https://XXXX.trycloudflare.com/mcp"
echo ""
# Not `exec`: keep the shell so the EXIT trap stops the server on Ctrl-C too.
"$CF" tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate
