<#
.SYNOPSIS
    Code quality check script for strata - equivalent to "Build" in C#.
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
    [switch]$Fix,
    [switch]$SkipDocsBuild
)

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

# ── 0. Clean stray .strata/ folders ─────────────────────────────────────────
# Tests and manual runs can leave .strata/ folders in places they shouldn't be.
# Only the config/ example workspaces should have them.
$strayStrata = Get-ChildItem -Path $projectRoot -Recurse -Directory -Filter ".strata" -Force |
Where-Object {
    $rel = $_.FullName.Substring($projectRoot.Length + 1)
    # Keep .strata in config/ example workspaces (they're part of the examples)
    $rel -notlike "config\*" -and
    # Keep the workspace root .strata/ (if this IS a strata workspace)
    $rel -ne ".strata"
}
if ($strayStrata.Count -gt 0) {
    Write-Host "[*] Cleaning stray .strata/ folders..." -ForegroundColor DarkYellow
    $strayStrata | ForEach-Object {
        Write-Host "    [-] $($_.FullName.Substring($projectRoot.Length + 1))" -ForegroundColor DarkYellow
        Remove-Item -Recurse -Force $_.FullName
    }
    Write-Host ""
}

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] strata - Code Quality Check" -ForegroundColor Cyan
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

# ── 3. Mypy (type check = compile equivalent) ───────────────────────────────
Write-Host "[*] Mypy type check..." -ForegroundColor Blue
uv run python -m mypy ./src ./tests
if ($LASTEXITCODE -ne 0) { $failed += "mypy" }
Write-Host ""

# ── 4. Smoke test ───────────────────────────────────────────────────────────
# Verify the CLI is importable and basic commands run without crashing.
# These run without a workspace — no deployment file required.
Write-Host "[*] Smoke test..." -ForegroundColor Blue
$smokeOk = $true

uv run python -m strata --help | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "    [!] strata --help exited $LASTEXITCODE" -ForegroundColor Red; $smokeOk = $false }
else { Write-Host "    [+] strata --help" -ForegroundColor Green }

uv run python -m strata version | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "    [!] strata version exited $LASTEXITCODE" -ForegroundColor Red; $smokeOk = $false }
else { Write-Host "    [+] strata version" -ForegroundColor Green }

uv run python -m strata tools status | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "    [!] strata tools status exited $LASTEXITCODE" -ForegroundColor Red; $smokeOk = $false }
else { Write-Host "    [+] strata tools status" -ForegroundColor Green }

if (-not $smokeOk) { $failed += "smoke test" }
Write-Host ""
# ── 5. Docs index coverage ──────────────────────────────────────────────────────────
# Verify every .md file under docs/ is referenced in index.rst.
# Excludes _build/, _static/, and underscore-prefixed files (temp/scratch docs).
Write-Host "[*] Docs index coverage..." -ForegroundColor Blue
$indexContent = Get-Content "$projectRoot\docs\index.rst" -Raw
$docsRoot = Join-Path $projectRoot "docs"
$excludeTopDirs = @("_build", "_static", "issues", "decisions", "help")
$missingFromIndex = @()

Get-ChildItem -Path $docsRoot -Recurse -Filter "*.md" | ForEach-Object {
    $rel = $_.FullName.Substring($docsRoot.Length + 1)   # e.g. guides\setup-azure-oidc.md
    $topDir = $rel.Split([IO.Path]::DirectorySeparatorChar)[0]
    if ($excludeTopDirs -contains $topDir) { return }

    # Skip underscore-prefixed files at any depth (temp/scratch docs)
    if ($_.Name -like '_*') { return }

    # Toctree entry format: forward slashes, no extension
    $entry = $rel.Replace('\', '/').Replace('.md', '')       # e.g. guides/setup-azure-oidc

    # -match is case-insensitive in PowerShell
    if ($indexContent -notmatch [regex]::Escape($entry)) {
        $missingFromIndex += $entry
    }
}

if ($missingFromIndex.Count -eq 0) {
    Write-Host "    [+] All docs files referenced in index.rst" -ForegroundColor Green
}
else {
    Write-Host "    [!] Not referenced in index.rst:" -ForegroundColor Red
    $missingFromIndex | ForEach-Object { Write-Host "        - $_" -ForegroundColor Yellow }
    $failed += "docs index coverage"
}
Write-Host ""

# ── 6. Sphinx docs build ─────────────────────────────────────────────────────────────
# Build the Sphinx HTML docs to catch broken references and missing pages.
# Pass -SkipDocsBuild to skip this step (e.g. when doc dependencies are not
# installed or in environments where the build is handled separately).
if ($SkipDocsBuild) {
    Write-Host "[*] Sphinx docs build... SKIPPED (-SkipDocsBuild)" -ForegroundColor DarkGray
}
else {
    Write-Host "[*] Sphinx docs build..." -ForegroundColor Blue
    uv sync --group doc 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    [!] Failed to install doc group dependencies" -ForegroundColor Red
        $failed += "sphinx docs build"
    }
    else {
        $sphinxOut = Join-Path ([System.IO.Path]::GetTempPath()) "strata-docs-check"
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
# ── 7. ADR 0030 migration guards ─────────────────────────────────────────────
# Regression guards for the BaseCommand lifecycle migration (ADR 0030 Option D).
# These patterns were deliberately eliminated; a failure here means a regression.
Write-Host "[*] ADR 0030 migration guards..." -ForegroundColor Blue
$guardOk = $true
$cmdSrc = Get-ChildItem -Path "$projectRoot\src\strata\commands" -Recurse -Filter "*.py"

# Guard 1: INIT_REQUIRED must never reappear — eliminated in Group F.
$initRequired = $cmdSrc | Select-String -Pattern "\bINIT_REQUIRED\b"
if ($initRequired) {
    Write-Host "    [!] INIT_REQUIRED re-introduced in:" -ForegroundColor Red
    $initRequired | ForEach-Object { Write-Host "        $($_.Filename):$($_.LineNumber)" -ForegroundColor Yellow }
    $guardOk = $false
}
else {
    Write-Host "    [+] No INIT_REQUIRED references" -ForegroundColor Green
}

# Guard 2: execute() must not be overridden in subclasses — only BaseCommand defines it.
$executeOverrides = $cmdSrc |
Where-Object { $_.Name -ne "base_command.py" } |
Select-String -Pattern "^\s+def execute\(self"
if ($executeOverrides) {
    Write-Host "    [!] execute() overrides found — use _execute() instead:" -ForegroundColor Red
    $executeOverrides | ForEach-Object { Write-Host "        $($_.Filename):$($_.LineNumber)" -ForegroundColor Yellow }
    $guardOk = $false
}
else {
    Write-Host "    [+] No execute() overrides in command subclasses" -ForegroundColor Green
}

# Guard 3: _run() must not reappear — bulk-renamed to _execute() in Groups A-E.
$runMethods = $cmdSrc | Select-String -Pattern "^\s+def _run\(self"
if ($runMethods) {
    Write-Host "    [!] _run() definitions found — rename to _execute():" -ForegroundColor Red
    $runMethods | ForEach-Object { Write-Host "        $($_.Filename):$($_.LineNumber)" -ForegroundColor Yellow }
    $guardOk = $false
}
else {
    Write-Host "    [+] No _run() method definitions in commands" -ForegroundColor Green
}

if (-not $guardOk) { $failed += "ADR 0030 migration guards" }
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
