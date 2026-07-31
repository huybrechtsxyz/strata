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

# ── 4. Pytest + coverage ─────────────────────────────────────────────────────
# Run the full test suite with coverage reporting. No hard --cov-fail-under
# threshold yet — this step exists to surface untested code paths in the
# standard check flow (see T1: a 5-command coverage gap went unnoticed for a
# long time because nothing here ever ran pytest).
Write-Host "[*] Pytest + coverage..." -ForegroundColor Blue
uv run python -m pytest -q --cov=src/strata --cov-report=term-missing
if ($LASTEXITCODE -ne 0) { $failed += "pytest" }
Write-Host ""

# ── 5. Smoke test ───────────────────────────────────────────────────────────
# Verify the CLI is importable and basic commands run without crashing.
# These run without a workspace — no deployment file required.
Write-Host "[*] Smoke test..." -ForegroundColor Blue
$smokeOk = $true

uv run python -m strata --help | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "    [!] strata --help exited $LASTEXITCODE" -ForegroundColor Red; $smokeOk = $false }
else { Write-Host "    [+] strata --help" -ForegroundColor Green }

uv run python -m strata --version | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "    [!] strata --version exited $LASTEXITCODE" -ForegroundColor Red; $smokeOk = $false }
else { Write-Host "    [+] strata --version" -ForegroundColor Green }

uv run python -m strata tools status | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "    [!] strata tools status exited $LASTEXITCODE" -ForegroundColor Red; $smokeOk = $false }
else { Write-Host "    [+] strata tools status" -ForegroundColor Green }

if (-not $smokeOk) { $failed += "smoke test" }
Write-Host ""
# ── 6. Docs index coverage ──────────────────────────────────────────────────────────
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

# ── 7. Sphinx docs build ─────────────────────────────────────────────────────────────
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
# ── 8. ADR 0030 migration guards ─────────────────────────────────────────────
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

# ── 9. Kind docs coverage ────────────────────────────────────────────────────
# Several docs hand-copy the list of valid `kind:` values instead of deriving it
# from PlatformKind. Verify each hand-typed list still matches reality, using
# `strata schema list --output json` (backed by PlatformKind/INTERNAL_KINDS in
# common_models.py) as the single source of truth.
Write-Host "[*] Kind docs coverage..." -ForegroundColor Blue
$kindDocsOk = $true
$schemaListJson = uv run strata schema list --output json 2>$null | ConvertFrom-Json
$allKinds = $schemaListJson.data.kinds | ForEach-Object { $_.kind }
$userKinds = $schemaListJson.data.kinds | Where-Object { -not $_.internal } | ForEach-Object { $_.kind } | Sort-Object

# Docs that must list EXACTLY the user-authorable kinds (no more, no less).
$exactMatchDocs = @(
    "docs\platform\commands.md",
    ".squad\templates\platform.instructions.md",
    ".github\copilot-instructions.md",
    ".github\instructions\strata.instructions.md"
)
foreach ($docPath in $exactMatchDocs) {
    $fullPath = Join-Path $projectRoot $docPath
    if (-not (Test-Path $fullPath)) { continue }
    $content = Get-Content $fullPath -Raw
    $lineMatch = [regex]::Match($content, 'Valid kinds:\s*\**\s*(.+)')
    if (-not $lineMatch.Success) {
        Write-Host "    [!] No 'Valid kinds' line found in $docPath" -ForegroundColor Red
        $kindDocsOk = $false
        continue
    }
    $tokens = [regex]::Matches($lineMatch.Groups[1].Value, '`([a-z0-9_-]+)`') | ForEach-Object { $_.Groups[1].Value } | Sort-Object
    $missing = $userKinds | Where-Object { $_ -notin $tokens }
    $extra = $tokens | Where-Object { $_ -notin $userKinds }
    if ($missing -or $extra) {
        Write-Host "    [!] $docPath 'Valid kinds' drifted from PlatformKind:" -ForegroundColor Red
        if ($missing) { Write-Host "        missing: $($missing -join ', ')" -ForegroundColor Yellow }
        if ($extra) { Write-Host "        unknown/internal: $($extra -join ', ')" -ForegroundColor Yellow }
        $kindDocsOk = $false
    }
}

# docs/GLOSSARY.md intentionally lists a broader set (including some internal
# kinds) — only check that every token it mentions is a REAL kind, catching
# invented ones (e.g. a since-removed `workflow`/`datacenter` typo).
$glossaryPath = Join-Path $projectRoot "docs\GLOSSARY.md"
if (Test-Path $glossaryPath) {
    $glossaryContent = Get-Content $glossaryPath -Raw
    $glossaryMatch = [regex]::Match($glossaryContent, 'Valid kinds:\s*(.+?)(?:\r?\n\r?\n|$)', 'Singleline')
    if ($glossaryMatch.Success) {
        $glossaryTokens = [regex]::Matches($glossaryMatch.Groups[1].Value, '`([a-z0-9_-]+)`') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
        $unknownTokens = $glossaryTokens | Where-Object { $_ -notin $allKinds }
        if ($unknownTokens) {
            Write-Host "    [!] docs\GLOSSARY.md lists kind(s) that don't exist in PlatformKind:" -ForegroundColor Red
            Write-Host "        $($unknownTokens -join ', ')" -ForegroundColor Yellow
            $kindDocsOk = $false
        }
    }
}

if ($kindDocsOk) {
    Write-Host "    [+] All 'Valid kinds' doc lists match PlatformKind" -ForegroundColor Green
}
else {
    $failed += "kind docs coverage"
}
Write-Host ""
# ── 10. strata-onboarding.md duplication drift ───────────────────────────────
# docs/skills/strata-onboarding.md (canonical, per ADR-0014) and
# .github/skills/strata-onboarding.md (Copilot-discoverable copy — the actual
# runtime location Copilot's skill-discovery reads) must stay byte-identical.
# They can't be a symlink (fragile on a Windows dev machine without git
# symlink support enabled) or a generated-at-build-time file (.github/skills/
# needs real content checked into git, not a build artifact), so this check
# is the enforcement mechanism instead. The third copy under
# src/strata/templates/solution/dot.github/skills/ is a scaffold template
# stamped into new user workspaces — intentionally a point-in-time snapshot,
# not kept in sync, and excluded from this check.
Write-Host "[*] strata-onboarding.md duplication drift..." -ForegroundColor Blue
$onboardingCanonical = Join-Path $projectRoot "docs\skills\strata-onboarding.md"
$onboardingCopy = Join-Path $projectRoot ".github\skills\strata-onboarding.md"
$onboardingDiff = Compare-Object (Get-Content $onboardingCanonical) (Get-Content $onboardingCopy)
if (-not $onboardingDiff) {
    Write-Host "    [+] docs/skills/ and .github/skills/ copies are identical" -ForegroundColor Green
}
else {
    Write-Host "    [!] docs/skills/strata-onboarding.md and .github/skills/strata-onboarding.md have diverged:" -ForegroundColor Red
    Write-Host "        Copy docs/skills/strata-onboarding.md over .github/skills/strata-onboarding.md (or vice versa) to resync." -ForegroundColor Yellow
    $failed += "strata-onboarding.md duplication drift"
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
