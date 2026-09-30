<#
.SYNOPSIS
    Code quality check script for strata-v2 - equivalent to "Build" in C#.
.DESCRIPTION
    Runs ruff (lint + format), mypy, import-linter (layering contract), pytest,
    a CLI smoke test, and (unless -SkipDocsBuild) a strict Sphinx docs build.
    Mirrors the exact check sequence CI runs (setup-python + test-python
    actions, plus ci-docs.yml) so a clean local run means CI will pass too.
    Exit code is non-zero if any check fails.
.PARAMETER Fix
    Auto-fix ruff lint and format issues where possible.
.PARAMETER SkipDocsBuild
    Skip the Sphinx docs build step (e.g. doc extras not installed).
.EXAMPLE
    .\scripts\Check.ps1
    .\scripts\Check.ps1 -Fix
    .\scripts\Check.ps1 -SkipDocsBuild
.NOTES
#>

param(
    [switch]$Fix,
    [switch]$SkipDocsBuild
)

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] strata-v2 - Code Quality Check" -ForegroundColor Cyan
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

# The corporate PyPI index uses first-index strategy by default, which prevents
# finding setuptools>=70.3.0 in the secondary index. Override for this script.
$prevIndexStrategy = $env:UV_INDEX_STRATEGY
$env:UV_INDEX_STRATEGY = "unsafe-best-match"

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

# ── 3. Mypy (type check = compile equivalent; src only, matches CI) ─────────
Write-Host "[*] Mypy type check..." -ForegroundColor Blue
uv run python -m mypy ./src
if ($LASTEXITCODE -ne 0) { $failed += "mypy" }
Write-Host ""

# ── 4. Import layering (lint-imports, ADR-0003) ─────────────────────────────
Write-Host "[*] Import layering (lint-imports)..." -ForegroundColor Blue
uv run lint-imports
if ($LASTEXITCODE -ne 0) { $failed += "lint-imports" }
Write-Host ""

# ── 5. Pytest ─────────────────────────────────────────────────────────────
Write-Host "[*] Pytest..." -ForegroundColor Blue
uv run pytest -q
if ($LASTEXITCODE -ne 0) { $failed += "pytest" }
Write-Host ""

# ── 6. Smoke test ───────────────────────────────────────────────────────────
# Verify the CLI is importable and basic commands run without crashing.
# These run without a workspace — no deployment file required.
Write-Host "[*] Smoke test..." -ForegroundColor Blue
$smokeOk = $true

uv run strata --help | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "    [!] strata --help exited $LASTEXITCODE" -ForegroundColor Red; $smokeOk = $false }
else { Write-Host "    [+] strata --help" -ForegroundColor Green }

uv run strata --version | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "    [!] strata --version exited $LASTEXITCODE" -ForegroundColor Red; $smokeOk = $false }
else { Write-Host "    [+] strata --version" -ForegroundColor Green }

uv run strata version | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "    [!] strata version exited $LASTEXITCODE" -ForegroundColor Red; $smokeOk = $false }
else { Write-Host "    [+] strata version" -ForegroundColor Green }

if (-not $smokeOk) { $failed += "smoke test" }
Write-Host ""

# ── 7. Sphinx docs build ─────────────────────────────────────────────────────
# Build the Sphinx HTML docs to catch broken references and missing pages.
# Pass -SkipDocsBuild to skip this step (e.g. when doc dependencies are not
# installed or in environments where the build is handled separately).
if ($SkipDocsBuild) {
    Write-Host "[*] Sphinx docs build... SKIPPED (-SkipDocsBuild)" -ForegroundColor DarkGray
}
else {
    Write-Host "[*] Sphinx docs build..." -ForegroundColor Blue
    uv sync --extra docs 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    [!] Failed to install doc extras" -ForegroundColor Red
        $failed += "sphinx docs build"
    }
    else {
        $sphinxOut = Join-Path ([System.IO.Path]::GetTempPath()) "strata-v2-docs-check"
        # -W: treat warnings as errors  --keep-going: collect all warnings, not just the first
        uv run python -m sphinx -b html -q -W --keep-going docs "$sphinxOut"
        if ($LASTEXITCODE -ne 0) {
            $failed += "sphinx docs build"
        }
        else {
            Write-Host "    [+] Sphinx build succeeded" -ForegroundColor Green
            Remove-Item -Recurse -Force $sphinxOut -ErrorAction SilentlyContinue
        }
    }
}
Write-Host ""

# ── Summary ─────────────────────────────────────────────────────────────────
# Restore the original index strategy
$env:UV_INDEX_STRATEGY = $prevIndexStrategy

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
