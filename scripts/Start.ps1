<#
.SYNOPSIS
    Starts the strata state-service server (ADR-0065) and, optionally, the
    read-only React dashboard dev server.
.DESCRIPTION
    1. Verifies the optional `server` extra (fastapi/uvicorn/sqlalchemy) is
       installed, installing it via `uv pip install -e ".[server]"` if not.
    2. Runs `strata serve migrate` against -DbUrl (idempotent — safe to run
       every time, `metadata.create_all(..., checkfirst=True)` under the hood).
    3. Opens the state-service server (`strata serve run`) in its own window.
    4. Unless -NoWebapp, opens the dashboard's `npm run dev` (src/webapp) in
       its own window too — installing node_modules first if missing.

    Both processes run in separate windows so their logs stay readable; close
    a window (or Ctrl+C inside it) to stop that process. This script itself
    returns as soon as both are launched.
.PARAMETER Port
    Port for the state-service server to bind. Default: 8443.
.PARAMETER BindHost
    Host for the state-service server to bind. Default: 127.0.0.1 (loopback).
    A non-loopback host requires TLS (ADR-0065 Step 2.1) — not exposed by this
    script; use `strata serve run` directly with `--tls-cert`/`--tls-key` for that.
.PARAMETER DbUrl
    Event-store connection URL. Default: sqlite:///./strata-state.db.
.PARAMETER AdminToken
    Admin bearer token, enabling the /v1/tokens management routes. Omit to
    leave those routes unregistered (the dashboard doesn't need them today).
.PARAMETER NoWebapp
    Skip starting the React dashboard's dev server — state-service only.
.PARAMETER NoMigrate
    Skip the `strata serve migrate` step (e.g. schema already applied).
.EXAMPLE
    .\scripts\Start.ps1
.EXAMPLE
    .\scripts\Start.ps1 -Port 8000 -NoWebapp
.EXAMPLE
    .\scripts\Start.ps1 -AdminToken dev-admin-token
.NOTES
#>
param(
    [int]$Port = 8443,
    [string]$BindHost = "127.0.0.1",
    [string]$DbUrl = "sqlite:///./strata-state.db",
    [string]$AdminToken,
    [switch]$NoWebapp,
    [switch]$NoMigrate
)

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] strata - Start local server" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

# Verify the optional `server` extra is installed (fastapi/uvicorn/sqlalchemy).
Write-Host "[*] Checking for the 'server' optional dependency..." -ForegroundColor Blue
uv run python -c "import fastapi, uvicorn, sqlalchemy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[*] Not found. Installing via: uv pip install -e `".[server]`"" -ForegroundColor Yellow
    uv pip install -e ".[server]"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] Failed to install the 'server' extra." -ForegroundColor Red
        exit 1
    }
    Write-Host "[+] 'server' extra installed." -ForegroundColor Green
}
else {
    Write-Host "[+] 'server' extra already installed." -ForegroundColor Green
}
Write-Host ""

# Apply/verify the event-store schema (safe to run repeatedly).
if (-not $NoMigrate) {
    Write-Host "[*] Applying event-store schema ($DbUrl)..." -ForegroundColor Blue
    uv run strata serve migrate --db-url $DbUrl --output json
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] serve migrate failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "[+] Schema applied." -ForegroundColor Green
    Write-Host ""
}

# Build the `strata serve run` command line.
$serveArgs = @("serve", "run", "--host", $BindHost, "--port", $Port, "--db-url", $DbUrl)
if ($AdminToken) {
    $serveArgs += @("--admin-token", $AdminToken)
}

Write-Host "[*] Starting state-service server on http://${BindHost}:${Port} ..." -ForegroundColor Blue
Start-Process pwsh -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$projectRoot'; uv run strata $($serveArgs -join ' ')"
)
Write-Host "[+] State-service server launched in a new window." -ForegroundColor Green
Write-Host ""

if (-not $NoWebapp) {
    $webappRoot = Join-Path $projectRoot "src\webapp"
    if (-not (Test-Path (Join-Path $webappRoot "node_modules"))) {
        Write-Host "[*] Installing dashboard dependencies (src/webapp)..." -ForegroundColor Blue
        Push-Location $webappRoot
        npm install
        $npmInstallExitCode = $LASTEXITCODE
        Pop-Location
        if ($npmInstallExitCode -ne 0) {
            Write-Host "[!] npm install failed." -ForegroundColor Red
            exit 1
        }
        Write-Host "[+] Dashboard dependencies installed." -ForegroundColor Green
        Write-Host ""
    }

    Write-Host "[*] Starting dashboard dev server (src/webapp)..." -ForegroundColor Blue
    Start-Process pwsh -ArgumentList @(
        "-NoExit", "-Command",
        "Set-Location '$webappRoot'; npm run dev"
    )
    Write-Host "[+] Dashboard dev server launched in a new window." -ForegroundColor Green
    Write-Host ""
}

Write-Host "[+] ==========================================" -ForegroundColor Cyan
Write-Host "[+] Server:    http://${BindHost}:${Port}" -ForegroundColor Green
if (-not $NoWebapp) {
    Write-Host "[+] Dashboard: http://localhost:5173 (see its window for the actual port)" -ForegroundColor Green
}
Write-Host "[+] Close each window (or Ctrl+C inside it) to stop." -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Cyan
