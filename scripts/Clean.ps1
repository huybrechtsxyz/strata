<#
.SYNOPSIS
    Cleans __pycache__ directories, .pyc files, and build artifacts.
.DESCRIPTION
    Recursively removes all __pycache__ directories, .pyc files, and common build artifact
    directories from the project root, excluding anything under the .venv directory.
.EXAMPLE
    .\scripts\Clean.ps1
#>

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] strata-v2 - Clean" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

$venvPath = Join-Path $projectRoot '.venv'

function Confirm-InVenv($path) {
    return $path -like "$venvPath*"
}

# ── 1. __pycache__ directories ───────────────────────────────────────────────
Write-Host "[*] Removing __pycache__ directories..." -ForegroundColor Blue
Get-ChildItem -Path $projectRoot -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
Where-Object { -not (Confirm-InVenv $_.FullName) } |
ForEach-Object {
    Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[+] Removed: $($_.FullName)" -ForegroundColor DarkGray
}
Write-Host ""

# ── 2. .pyc files ────────────────────────────────────────────────────────────
Write-Host "[*] Removing .pyc files..." -ForegroundColor Blue
Get-ChildItem -Path $projectRoot -Recurse -Include *.pyc -ErrorAction SilentlyContinue |
Where-Object { -not (Confirm-InVenv $_.FullName) } |
ForEach-Object {
    Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
    Write-Host "[+] Removed: $($_.FullName)" -ForegroundColor DarkGray
}
Write-Host ""

# ── 3. Build artifact directories ────────────────────────────────────────────
Write-Host "[*] Removing build artifact directories..." -ForegroundColor Blue
$buildDirs = @(
    "build", "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".import_linter_cache", ".tox", ".coverage", ".eggs", "*.egg-info", ".cache"
)
foreach ($dir in $buildDirs) {
    Get-ChildItem -Path $projectRoot -Recurse -Directory -Filter $dir -ErrorAction SilentlyContinue |
    Where-Object { -not (Confirm-InVenv $_.FullName) } |
    ForEach-Object {
        Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "[+] Removed: $($_.FullName)" -ForegroundColor DarkGray
    }
}
Write-Host ""

# ── 4. Stray .strata/ folders ────────────────────────────────────────────────
# Tests and manual runs can leave .strata/ folders in places they shouldn't be.
# Only the config/ example workspace and known migration fixtures keep theirs.
Write-Host "[*] Removing stray .strata/ folders..." -ForegroundColor Blue
Get-ChildItem -Path $projectRoot -Recurse -Directory -Filter ".strata" -Force -ErrorAction SilentlyContinue |
Where-Object {
    $rel = $_.FullName.Substring($projectRoot.Length + 1)
    $rel -notlike "config\*" -and $rel -notlike ".v2-*\*"
} |
ForEach-Object {
    Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[+] Removed: $($_.FullName)" -ForegroundColor DarkGray
}
Write-Host ""

Write-Host "[+] ==========================================" -ForegroundColor Cyan
Write-Host "[+] Clean complete." -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Cyan
