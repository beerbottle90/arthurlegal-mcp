#requires -Version 5
<#
  run-public.ps1 — Windows PowerShell equivalent of run-public.sh.

  Expose the eqanun-api MCP server over a public HTTPS URL using a
  Cloudflare quick tunnel (free, no account, no ngrok interstitial).

  Usage (Windows PowerShell 5.1 or PowerShell 7), from the repo root:
      cd eqanun-api
      powershell -ExecutionPolicy Bypass -File .\run-public.ps1

  (The default Restricted execution policy blocks unsigned scripts; the line
  above runs it without changing your machine's policy.)

  Then paste the printed  https://<something>.trycloudflare.com/mcp  URL into the
  Copilot Studio MCP connector (copilot-studio\mcp-connector.swagger.json -> host),
  or add it as a claude.ai custom connector. No auth.

  THIRD-PARTY BINARY:
    This needs cloudflared. It uses one already on your PATH if present —
    preferred, because your installer verified it. Otherwise it will download
    the pinned release from Cloudflare's GitHub into .tools\ and run it, and it
    REFUSES to do so unless you opt in:
        $env:EQANUN_ALLOW_DOWNLOAD = '1' ; .\run-public.ps1
    Cloudflare publishes no checksum file for its releases, so an auto-download
    CANNOT be integrity-verified here. `winget install Cloudflare.cloudflared`
    is the safer path.

  SECURITY: this publishes an UNAUTHENTICATED MCP server on the public internet
  for as long as the script runs. Anyone with the URL can call its tools. Stop
  the script when you are done.

  Notes:
    - A quick-tunnel URL CHANGES every run and is "best effort". For a STABLE URL,
      use a named Cloudflare tunnel or Tailscale Funnel (see copilot-studio\RUNBOOK.md).
    - Ctrl-C stops both the tunnel and the MCP server.
    - Override the port with:  $env:EQANUN_PORT = 9000 ; .\run-public.ps1
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # speeds up Invoke-WebRequest a lot on PS 5.1

$cfVersion = if ($env:CLOUDFLARED_VERSION) { $env:CLOUDFLARED_VERSION } else { '2026.7.3' }
$port  = if ($env:EQANUN_PORT) { $env:EQANUN_PORT } else { 8000 }
$here  = $PSScriptRoot
$tools = Join-Path $here '.tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null

# 0) Resolve python (the Store alias stub does not count).
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python -or $python.Source -like '*WindowsApps*') {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Error "python not found on PATH. Install Python 3.9+ and re-run."
    exit 1
}
$pythonExe = $python.Source

# 1) Bootstrap cloudflared.exe into .tools\ if not already present.
$systemCf = Get-Command cloudflared -ErrorAction SilentlyContinue
$localCf  = Join-Path $tools 'cloudflared.exe'

if ($systemCf) {
    $cf = $systemCf.Source
    Write-Host "Using cloudflared from PATH: $cf"
} elseif (Test-Path $localCf) {
    $cf = $localCf
    Write-Host "Using previously downloaded $cf"
} else {
    $cf = $localCf
    if ($env:EQANUN_ALLOW_DOWNLOAD -ne '1') {
        Write-Host ""
        Write-Host "cloudflared is not installed and no downloaded copy exists." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "This script can download it from Cloudflare's GitHub releases, but Cloudflare"
        Write-Host "publishes no checksum file, so the download CANNOT be integrity-verified."
        Write-Host ""
        Write-Host "Preferred - install it yourself:"
        Write-Host "    winget install Cloudflare.cloudflared"
        Write-Host ""
        Write-Host "Or accept the unverified download explicitly:"
        Write-Host "    `$env:EQANUN_ALLOW_DOWNLOAD = '1' ; .\run-public.ps1"
        Write-Host ""
        Write-Host "Or skip this script entirely:"
        Write-Host "    python server.py --transport http --port 8000"
        Write-Host "    ...then front it with your own tunnel or reverse proxy."
        exit 1
    }

    $arch  = if ([Environment]::Is64BitOperatingSystem) { 'amd64' } else { '386' }
    $asset = "cloudflared-windows-$arch.exe"
    $base  = "https://github.com/cloudflare/cloudflared/releases/download/$cfVersion"
    Write-Host "Downloading cloudflared $cfVersion ($asset) into .tools\ - UNVERIFIED."
    Invoke-WebRequest -Uri "$base/$asset" -OutFile $cf
    Write-Host "SHA-256 of what was downloaded (record it if you care):"
    Write-Host "  $((Get-FileHash -Path $cf -Algorithm SHA256).Hash.ToLower())"
}

# 2) Start the MCP server (hidden background process); log to .tools\.
$serverPy = Join-Path $here 'server.py'
$errLog   = Join-Path $tools 'server.err.log'
$outLog   = Join-Path $tools 'server.out.log'
Write-Host "Starting MCP server on http://127.0.0.1:$port/mcp ..."
$server = Start-Process -FilePath $pythonExe `
    -ArgumentList @("`"$serverPy`"", '--transport', 'http', '--host', '127.0.0.1', '--port', "$port") `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardError $errLog -RedirectStandardOutput $outLog

try {
    Start-Sleep -Seconds 2
    if ($server.HasExited) {
        Write-Error "MCP server failed to start. See $errLog"
        Get-Content $errLog -Tail 20 -ErrorAction SilentlyContinue
        exit 1
    }
    Write-Host ""
    Write-Host ">>> When the box below shows https://XXXX.trycloudflare.com , your MCP URL is:" -ForegroundColor Cyan
    Write-Host ">>>     https://XXXX.trycloudflare.com/mcp" -ForegroundColor Cyan
    Write-Host ""
    # Foreground tunnel; cloudflared prints the public URL in a box. Ctrl-C stops it.
    & $cf tunnel --url "http://127.0.0.1:$port" --no-autoupdate
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "MCP server stopped."
}
