<#
.SYNOPSIS
    Documentation build script for strata-v2.
.DESCRIPTION
    Builds the Sphinx HTML documentation into docs/_build/html. Run Setup.ps1
    first to ensure the virtual environment exists.
.PARAMETER Strict
    Treat Sphinx warnings as errors (-W --keep-going) — matches what a
    Check.ps1 run does. Omit for a fast, warning-tolerant local preview build.
.EXAMPLE
    .\scripts\Docs.ps1
    .\scripts\Docs.ps1 -Strict
.NOTES
#>
param(
    [switch]$Strict
)

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] strata-v2 - Build Documentation" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

# Ensure virtual environment is active
if (-not (Test-Path "$projectRoot\.venv\Scripts\Activate.ps1")) {
    Write-Host "[!] Virtual environment not found. Run Setup.ps1 first." -ForegroundColor Red
    exit 1
}

if (-not $env:VIRTUAL_ENV) {
    & "$projectRoot\.venv\Scripts\Activate.ps1"
}

# ── 1. Ensure doc dependencies are installed ────────────────────────────────
Write-Host "[*] Installing documentation dependencies..." -ForegroundColor Blue
uv sync --extra docs
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Failed to install documentation dependencies." -ForegroundColor Red
    exit 1
}
Write-Host ""

# ── 2. Build Sphinx HTML ─────────────────────────────────────────────────────
$outDir = Join-Path $projectRoot "docs\_build\html"
Write-Host "[*] Building Sphinx HTML documentation..." -ForegroundColor Blue
if ($Strict) {
    uv run python -m sphinx -b html docs "$outDir" -W --keep-going
}
else {
    uv run python -m sphinx -b html docs "$outDir" -q
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Documentation build failed." -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "[+] ==========================================" -ForegroundColor Green
Write-Host "[+] Documentation built successfully." -ForegroundColor Green
Write-Host "[+] Output: $outDir" -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Green
