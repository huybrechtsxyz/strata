<#
.SYNOPSIS
    Code quality check script for XYZ Platform - equivalent to "Build" in C#.
.DESCRIPTION
    Runs ruff (lint + format), mypy (type checking), and optionally builds the package.
    Exit code is non-zero if any check fails.
.PARAMETER Fix
    Auto-fix ruff lint and format issues where possible.
.EXAMPLE
    .\Check.ps1
    .\Check.ps1 -Fix
.NOTES
#>

param(
    [switch]$Fix
)

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] XYZ Platform - Code Quality Check" -ForegroundColor Cyan
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

# Track overall result
$failed = @()

# ── 1. Ruff lint ────────────────────────────────────────────────────────────
Write-Host "[*] Ruff lint..." -ForegroundColor Blue
if ($Fix) {
    uv run ruff check --fix ./src ./tests
}
else {
    uv run ruff check ./src ./tests
}
if ($LASTEXITCODE -ne 0) { $failed += "ruff lint" }
Write-Host ""

# ── 2. Ruff format ──────────────────────────────────────────────────────────
Write-Host "[*] Ruff format..." -ForegroundColor Blue
if ($Fix) {
    uv run ruff format ./src ./tests
}
else {
    uv run ruff format --check ./src ./tests
}
if ($LASTEXITCODE -ne 0) { $failed += "ruff format" }
Write-Host ""

# ── 3. Mypy (type check = compile equivalent) ───────────────────────────────
Write-Host "[*] Mypy type check..." -ForegroundColor Blue
uv run python -m mypy ./src ./tests
if ($LASTEXITCODE -ne 0) { $failed += "mypy" }
Write-Host ""

# ── Summary ─────────────────────────────────────────────────────────────────
if ($failed.Count -eq 0) {
    Write-Host "[+] ================================================" -ForegroundColor Cyan
    Write-Host "[+] All checks passed!" -ForegroundColor Green
    Write-Host "[+] ================================================" -ForegroundColor Cyan
    exit 0
}
else {
    Write-Host "[!] ================================================" -ForegroundColor Red
    Write-Host "[!] The following checks failed:" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    Write-Host "[!] ================================================" -ForegroundColor Red
    exit 1
}
