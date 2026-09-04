#!/usr/bin/env bash
#
# Expose the eqanun-api MCP server over a public HTTPS URL using a Cloudflare
# quick tunnel (free, no account, no interstitial). macOS and Linux.
#
# Usage:
#   ./run-public.sh
# then paste the printed  https://<something>.trycloudflare.com/mcp  URL into the
# Copilot Studio MCP connector (mcp-connector.swagger.json -> host), No auth.
#
# THIRD-PARTY BINARY:
#   This needs `cloudflared`. It uses one already on your PATH if present —
#   preferred, because your package manager verified it. Otherwise it will
#   download the pinned release from Cloudflare's GitHub into ./.tools/ and run
#   it, and it will REFUSE to do so unless you opt in with:
#       EQANUN_ALLOW_DOWNLOAD=1 ./run-public.sh
#   Cloudflare publishes no checksum file for its releases, so an auto-download
#   CANNOT be integrity-verified here. Installing cloudflared yourself (brew /
#   apt / the official .deb/.rpm) is the safer path.
#
# SECURITY: this publishes an UNAUTHENTICATED MCP server on the public internet
# for as long as the script runs. Anyone with the URL can call its tools. The
# server is read-only against a public database, but treat the URL as sensitive
# and stop the script when you are done.
#
# Notes on stability:
#   - A quick-tunnel URL CHANGES every run and is "best effort". For a STABLE URL
#     switch to a named Cloudflare tunnel or Tailscale Funnel — see
#     copilot-studio/RUNBOOK.md.
#
set -euo pipefail

CF_VERSION="${CLOUDFLARED_VERSION:-2026.7.3}"
PORT="${EQANUN_PORT:-8000}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$HERE/.tools"
mkdir -p "$TOOLS"

command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }

OS="$(uname -s)"
case "$(uname -m)" in
  arm64|aarch64) ARCH=arm64 ;;
  *)             ARCH=amd64 ;;
esac

# 1) Find cloudflared: prefer one already installed, then a previous download.
if command -v cloudflared >/dev/null 2>&1; then
  CF="$(command -v cloudflared)"
  echo "Using cloudflared from PATH: $CF"
elif [ -x "$TOOLS/cloudflared" ]; then
  CF="$TOOLS/cloudflared"
  echo "Using previously downloaded $CF"
else
  CF="$TOOLS/cloudflared"
  if [ "${EQANUN_ALLOW_DOWNLOAD:-0}" != "1" ]; then
    cat >&2 <<'MSG'
cloudflared is not installed and no downloaded copy exists.

This script can download it from Cloudflare's GitHub releases, but Cloudflare
publishes no checksum file, so the download CANNOT be integrity-verified.

Preferred — install it yourself, verified by your package manager:
    macOS:  brew install cloudflared
    Linux:  see https://pkg.cloudflare.com/  (apt/yum repo, signed packages)

Or accept the unverified download explicitly:
    EQANUN_ALLOW_DOWNLOAD=1 ./run-public.sh

Or skip this script entirely:
    python3 server.py --transport http --port 8000
    ...then front it with your own tunnel or reverse proxy.
MSG
    exit 1
  fi

  base="https://github.com/cloudflare/cloudflared/releases/download/$CF_VERSION"
  case "$OS" in
    Darwin) asset="cloudflared-darwin-$ARCH.tgz" ;;
    Linux)  asset="cloudflared-linux-$ARCH" ;;
    *) echo "Unsupported OS: $OS. On Windows use run-public.ps1." >&2; exit 1 ;;
  esac

  echo "Downloading cloudflared $CF_VERSION ($asset) into .tools/ — UNVERIFIED."
  if [ "$OS" = "Darwin" ]; then
    curl -fsSL -o "$TOOLS/cf.tgz" "$base/$asset"
    tar -xzf "$TOOLS/cf.tgz" -C "$TOOLS"
    rm -f "$TOOLS/cf.tgz"
  else
    curl -fsSL -o "$CF" "$base/$asset"
  fi
  chmod +x "$CF"
  echo "SHA-256 of what was downloaded (record it if you care):"
  if command -v sha256sum >/dev/null; then sha256sum "$CF"; else shasum -a 256 "$CF"; fi
fi

# 2) Start the MCP server in the background, bound to loopback.
echo "Starting MCP server on http://127.0.0.1:$PORT/mcp ..."
if [ "$OS" = "Darwin" ]; then
  # Keep the Mac awake while the tunnel is up (a CLOSED lid still sleeps unless
  # configured in System Settings).
  caffeinate -is python3 "$HERE/server.py" --transport http --host 127.0.0.1 --port "$PORT" &
else
  python3 "$HERE/server.py" --transport http --host 127.0.0.1 --port "$PORT" &
fi
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
