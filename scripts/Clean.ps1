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
Write-Host "[*] XYZ Platform - Clean" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

$venvPath = Join-Path $projectRoot '.venv'

function Confirm-InVenv($path) {
    return $path -like "$venvPath*"
}

# ── 1. __pycache__ directories ───────────────────────────────────────────────
Write-Host "[*] Removing __pycache__ directories..." -ForegroundColor Blue
Get-ChildItem -Path $projectRoot -Recurse -Directory -Filter "__pycache__" |
Where-Object { -not (Confirm-InVenv $_.FullName) } |
ForEach-Object {
    Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[+] Removed: $($_.FullName)" -ForegroundColor DarkGray
}
Write-Host ""

# ── 2. .pyc files ────────────────────────────────────────────────────────────
Write-Host "[*] Removing .pyc files..." -ForegroundColor Blue
Get-ChildItem -Path $projectRoot -Recurse -Include *.pyc |
Where-Object { -not (Confirm-InVenv $_.FullName) } |
ForEach-Object {
    Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
    Write-Host "[+] Removed: $($_.FullName)" -ForegroundColor DarkGray
}
Write-Host ""

# ── 3. Build artifact directories ────────────────────────────────────────────
Write-Host "[*] Removing build artifact directories..." -ForegroundColor Blue
$buildDirs = @("build", "dist", ".pytest_cache", ".mypy_cache", ".tox", ".coverage", ".eggs", "*.egg-info", ".cache")
foreach ($dir in $buildDirs) {
    Get-ChildItem -Path $projectRoot -Recurse -Directory -Filter $dir |
    Where-Object { -not (Confirm-InVenv $_.FullName) } |
    ForEach-Object {
        Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "[+] Removed: $($_.FullName)" -ForegroundColor DarkGray
    }
}
Write-Host ""

Write-Host "[+] ==========================================" -ForegroundColor Cyan
Write-Host "[+] Clean complete." -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Cyan