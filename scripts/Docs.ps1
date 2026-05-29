<#
.SYNOPSIS
    Documentation build script for XYZ Platform.
.DESCRIPTION
    Converts README.md to RST with pandoc, then builds the Sphinx HTML
    documentation into .app/docs/ under the project root.
    Run Setup.ps1 first to ensure the virtual environment exists.
.EXAMPLE
    .\Docs.ps1
.NOTES
#>

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] XYZ Platform - Build Documentation" -ForegroundColor Cyan
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

# ── 1. Sync doc dependencies ────────────────────────────────────────────────
Write-Host "[*] Installing documentation dependencies..." -ForegroundColor Blue
uv sync --frozen --group doc
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Failed to install documentation dependencies." -ForegroundColor Red
    exit 1
}
Write-Host ""

# ── 2. Prepare output directory ─────────────────────────────────────────────
$outDir = Join-Path $projectRoot ".app\docs"
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}
Write-Host "[+] Output directory: $outDir" -ForegroundColor Green
Write-Host ""

# ── 3. Convert README.md → docs/README.rst ──────────────────────────────────
Write-Host "[*] Converting README.md to RST..." -ForegroundColor Blue
if (Get-Command pandoc -ErrorAction SilentlyContinue) {
    pandoc README.md -o docs/README.rst
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] pandoc conversion failed — continuing without README.rst." -ForegroundColor Yellow
    }
    else {
        Write-Host "[+] docs/README.rst generated." -ForegroundColor Green
    }
}
else {
    Write-Host "[!] pandoc not found — skipping README.rst generation." -ForegroundColor Yellow
    Write-Host "[!] Install pandoc from https://pandoc.org/installing.html" -ForegroundColor Yellow
}
Write-Host ""

# ── 4. Build Sphinx HTML ─────────────────────────────────────────────────────
Write-Host "[*] Building Sphinx HTML documentation..." -ForegroundColor Blue
uv run python -m sphinx -b html docs/ "$outDir" -W --keep-going
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Documentation build failed." -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "[+] ==========================================" -ForegroundColor Green
Write-Host "[+] Documentation built successfully." -ForegroundColor Green
Write-Host "[+] Output: $outDir" -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Green
