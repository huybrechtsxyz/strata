<#
.SYNOPSIS
    Setup script for strata-v2 on Windows.
.DESCRIPTION
    Installs uv, creates a virtual environment, and installs project dependencies
    (including dev extras) via `uv sync`.
.PARAMETER UseProGet
    Register the internal ProGet package index in uv's config. Use this on work machines.
    Omit on home machines to resolve packages directly from PyPI.
.EXAMPLE
    .\scripts\Setup.ps1
    .\scripts\Setup.ps1 -UseProGet
.NOTES
    `uv sync` installs the `dev` dependency-group by default (uv's default-group
    behavior) — this is exactly what CI's setup-python action runs, so it's the
    proven-correct way to get mypy/ruff/pytest/import-linter installed here.
#>
param(
    [switch]$UseProGet
)

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] strata-v2 - Setup" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

# Check execution policy
$executionPolicy = Get-ExecutionPolicy -Scope CurrentUser
if ($executionPolicy -eq "Restricted") {
    Write-Host "[*] Setting execution policy to RemoteSigned..." -ForegroundColor Yellow
    try {
        Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
        Write-Host "[+] Execution policy updated." -ForegroundColor Green
    }
    catch {
        Write-Host "[!] Failed to update execution policy. Run as administrator if needed." -ForegroundColor Red
    }
    Write-Host ""
}

# Install uv if not already available
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[*] Installing uv..." -ForegroundColor Blue
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # Refresh PATH for current session
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH
    Write-Host "[+] uv installed." -ForegroundColor Green
    Write-Host ""
}
else {
    Write-Host "[+] uv already available: $(uv --version)" -ForegroundColor Green
    Write-Host ""
}

# Create virtual environment
Write-Host "[*] Creating virtual environment with uv..." -ForegroundColor Blue
uv venv "$projectRoot\.venv"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Failed to create virtual environment." -ForegroundColor Red
    exit 1
}
Write-Host "[+] Virtual environment created." -ForegroundColor Green
Write-Host ""

# Activate virtual environment
Write-Host "[*] Activating virtual environment..." -ForegroundColor Blue
& "$projectRoot\.venv\Scripts\Activate.ps1"
Write-Host "[+] Virtual environment activated." -ForegroundColor Green
Write-Host ""

# Configure uv to use the internal ProGet package index (work machines only).
# At home, skip this block and resolve packages directly from PyPI.
# Run Setup.ps1 -UseProGet on work machines to register the internal index.
if ($UseProGet) {
    Write-Host "[*] Configuring uv package index (ProGet)..." -ForegroundColor Blue
    $uvConfigDir = "$env:APPDATA\uv"
    $uvConfigFile = "$uvConfigDir\uv.toml"
    if (-not (Test-Path $uvConfigDir)) {
        New-Item -Path $uvConfigDir -ItemType Directory -Force | Out-Null
    }
    if (-not (Test-Path $uvConfigFile)) {
        @"
[[index]]
url = "https://omhqproget.domain.ompartners.com/pypi/Dev-PyPI-OSS/simple"
"@ | Set-Content -Path $uvConfigFile -Encoding UTF8
        Write-Host "[+] uv config created at $uvConfigFile" -ForegroundColor Green
    }
    else {
        Write-Host "[+] uv config already exists at $uvConfigFile - skipping." -ForegroundColor Green
    }
    Write-Host ""
}
else {
    $uvConfigDir = "$env:APPDATA\uv"
    $uvConfigFile = "$uvConfigDir\uv.toml"
    if (Test-Path $uvConfigFile) {
        Remove-Item -Path $uvConfigFile -Force
        Write-Host "[*] Removed stale ProGet uv config (resolving from PyPI)." -ForegroundColor Yellow
    }
    else {
        Write-Host "[*] No ProGet config present (resolving from PyPI)." -ForegroundColor Yellow
    }
    Write-Host ""
}

# Install project dependencies, incl. the default `dev` dependency-group
# (mypy/ruff/pytest/import-linter/nox) — matches CI's setup-python action.
Write-Host "[*] Syncing dependencies (uv sync)..." -ForegroundColor Blue
$env:UV_INDEX_STRATEGY = "unsafe-best-match"
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Failed to sync dependencies." -ForegroundColor Red
    exit 1
}
Write-Host "[+] Dependencies installed." -ForegroundColor Green
Write-Host ""

Write-Host "[+] ================================================" -ForegroundColor Cyan
Write-Host "[+] Setup completed successfully!" -ForegroundColor Green
Write-Host "[+] ================================================" -ForegroundColor Cyan
