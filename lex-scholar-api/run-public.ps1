#requires -Version 5
<#
  run-public.ps1 — Windows PowerShell equivalent of run-public.sh.

  Expose the lex-scholar-api MCP server over a public HTTPS URL using a
  Cloudflare quick tunnel (free, no account, no ngrok interstitial).

  Usage (Windows PowerShell 5.1 or PowerShell 7):
      cd lex-scholar-api
      .\run-public.ps1

  Then paste the printed  https://<something>.trycloudflare.com/mcp  URL into the
  Copilot Studio MCP connector (copilot-studio\mcp-connector.swagger.json -> host),
  or add it as a claude.ai custom connector. No auth.

  Notes:
    - A quick-tunnel URL CHANGES every run and is "best effort". For a STABLE URL,
      use a named Cloudflare tunnel or Tailscale Funnel (see copilot-studio\RUNBOOK.md).
    - Ctrl-C stops both the tunnel and the MCP server.
    - Override the port with:  $env:LEXSCHOLAR_PORT = 9000 ; .\run-public.ps1
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # speeds up Invoke-WebRequest a lot on PS 5.1

$port  = if ($env:LEXSCHOLAR_PORT) { $env:LEXSCHOLAR_PORT } else { 8000 }
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
$cf = Join-Path $tools 'cloudflared.exe'
if (-not (Test-Path $cf)) {
    $arch = if ([Environment]::Is64BitOperatingSystem) { 'amd64' } else { '386' }
    $url  = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-$arch.exe"
    Write-Host "Downloading cloudflared (windows-$arch) into .tools\ ..."
    Invoke-WebRequest -Uri $url -OutFile $cf
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
