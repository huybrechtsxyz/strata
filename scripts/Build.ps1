<#
.SYNOPSIS
    Builds the strata wheel and installs it as a global uv tool.
.DESCRIPTION
    Runs `uv build --wheel` then installs from the resulting .whl file.

    Do NOT use `uv tool install .` directly — uv caches wheels built from local
    source paths and may silently reuse a stale wheel, meaning newly added or
    moved package-data files (e.g. templates) will be missing from the installed
    tool until you run this script.
.EXAMPLE
    .\scripts\Build.ps1
.NOTES
#>

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] Strata - Build & Install" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

# Build wheel
Write-Host "[*] Building wheel..." -ForegroundColor Blue
uv build --wheel
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] uv build failed." -ForegroundColor Red
    exit 1
}

$wheel = Get-ChildItem "$projectRoot\dist" -Filter "*.whl" |
Sort-Object LastWriteTime -Descending |
Select-Object -First 1

if (-not $wheel) {
    Write-Host "[!] No wheel found in dist/ after build." -ForegroundColor Red
    exit 1
}

Write-Host "[+] Built: $($wheel.Name)" -ForegroundColor Green
Write-Host ""

# Install as global tool from the wheel (bypasses uv's source-path cache)
Write-Host "[*] Installing strata as global tool..." -ForegroundColor Blue
uv tool install $wheel.FullName --force
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] uv tool install failed." -ForegroundColor Red
    exit 1
}

Write-Host "[+] ==========================================" -ForegroundColor Cyan
Write-Host "[+] strata installed: $(strata version)" -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Cyan
