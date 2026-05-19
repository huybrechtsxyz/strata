<#
.SYNOPSIS
    Rename layer 1: directory structure + pyproject.toml package identity.
.DESCRIPTION
    Renames src/xyz_platform -> src/strata and tests/xyz_platform -> tests/strata.
    Updates pyproject.toml: package name, entry points, package-data keys.
    Does NOT touch Python imports (that is layer 2: Rename-Layer2.ps1).
.NOTES
    Run from the repo root on the rename branch.
    Safe to re-run: checks before moving.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[*] Layer 1 rename: xyz_platform -> strata" -ForegroundColor Cyan
Write-Host "[*] Root: $root" -ForegroundColor Cyan
Write-Host ""

# ── 1. Delete stale build artifacts ─────────────────────────────────────────
Write-Host "[*] Cleaning stale build artifacts..." -ForegroundColor Blue

foreach ($dir in @("build", "src\xyz_platform.egg-info", "src\strata.egg-info")) {
    $path = Join-Path $root $dir
    if (Test-Path $path) {
        Remove-Item -Recurse -Force $path
        Write-Host "[+] Deleted: $dir" -ForegroundColor Green
    }
}
Write-Host ""

# ── 2. Rename src/xyz_platform -> src/strata ────────────────────────────────
Write-Host "[*] Renaming src/xyz_platform -> src/strata..." -ForegroundColor Blue

$srcOld = Join-Path $root "src\xyz_platform"
$srcNew = Join-Path $root "src\strata"

if (Test-Path $srcNew) {
    Write-Host "[!] src/strata already exists — skipping rename." -ForegroundColor Yellow
}
elseif (-not (Test-Path $srcOld)) {
    Write-Host "[!] src/xyz_platform not found — already renamed?" -ForegroundColor Yellow
}
else {
    Rename-Item -Path $srcOld -NewName "strata"
    Write-Host "[+] src/xyz_platform -> src/strata" -ForegroundColor Green
}
Write-Host ""

# ── 3. Rename tests/xyz_platform -> tests/strata ────────────────────────────
Write-Host "[*] Renaming tests/xyz_platform -> tests/strata..." -ForegroundColor Blue

$testsOld = Join-Path $root "tests\xyz_platform"
$testsNew = Join-Path $root "tests\strata"

if (Test-Path $testsNew) {
    Write-Host "[!] tests/strata already exists — skipping rename." -ForegroundColor Yellow
}
elseif (-not (Test-Path $testsOld)) {
    Write-Host "[!] tests/xyz_platform not found — already renamed?" -ForegroundColor Yellow
}
else {
    Rename-Item -Path $testsOld -NewName "strata"
    Write-Host "[+] tests/xyz_platform -> tests/strata" -ForegroundColor Green
}
Write-Host ""

# ── 4. Update pyproject.toml ─────────────────────────────────────────────────
Write-Host "[*] Updating pyproject.toml..." -ForegroundColor Blue

$toml = Join-Path $root "pyproject.toml"
$content = Get-Content $toml -Raw

# Project name (only the [project] name field, not urls or other fields)
$content = $content -replace '(?m)^name = "xyz-platform"', 'name = "strata"'

# Entry points
$content = $content -replace '(?m)^xyz-platform = "xyz_platform\.cli:main"', 'strata = "strata.cli:main"'
$content = $content -replace '(?m)^xyz = "xyz_platform\.cli:main"', 'xyz = "strata.cli:main"'

# Package-data keys: "xyz_platform.X" -> "strata.X"
$content = $content -replace '"xyz_platform\.([^"]+)"', '"strata.$1"'

Set-Content $toml $content -NoNewline
Write-Host "[+] pyproject.toml updated" -ForegroundColor Green
Write-Host ""

# ── 5. Summary ───────────────────────────────────────────────────────────────
Write-Host "[+] ==========================================" -ForegroundColor Green
Write-Host "[+] Layer 1 complete." -ForegroundColor Green
Write-Host "[+] Next step: run Rename-Layer2.ps1 to fix Python imports." -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Green
