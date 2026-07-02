<#
.SYNOPSIS
    Builds the strata wheel and VS Code extension, then installs both.
.DESCRIPTION
    1. Runs `uv build --wheel` then installs the wheel as a global uv tool.
    2. Runs `npm ci` + `npx tsc` inside src/vscode/, packages a .vsix with
       `vsce package`, copies it to dist/, and installs it into VS Code.

    Do NOT use `uv tool install .` directly — uv caches wheels built from local
    source paths and may silently reuse a stale wheel, meaning newly added or
    moved package-data files (e.g. templates) will be missing from the installed
    tool until you run this script.
.PARAMETER SkipExtension
    Skip the VS Code extension build/install step.
.EXAMPLE
    .\scripts\Build.ps1
.EXAMPLE
    .\scripts\Build.ps1 -SkipExtension
.NOTES
#>
param(
    [switch]$SkipExtension
)

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] Strata - Build & Install" -ForegroundColor Cyan
if ($SkipExtension) {
    Write-Host "[*] (VS Code extension: skipped)" -ForegroundColor DarkGray
}
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

# ── VS Code extension ──────────────────────────────────────────────────────

if ($SkipExtension) {
    Write-Host ""
    Write-Host "[*] Skipping VS Code extension build." -ForegroundColor DarkGray
    exit 0
}

Write-Host ""
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] VS Code Extension - Build & Install" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

$extensionRoot = Join-Path $projectRoot "src\vscode"

# Verify npm is available
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "[!] npm not found. Install Node.js to build the VS Code extension." -ForegroundColor Red
    exit 1
}

# Install Node dependencies
Write-Host "[*] Installing Node dependencies..." -ForegroundColor Blue
Push-Location $extensionRoot
npm ci --prefer-offline
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "[!] npm ci failed." -ForegroundColor Red
    exit 1
}
Write-Host "[+] Node dependencies installed." -ForegroundColor Green
Write-Host ""

# Compile TypeScript
Write-Host "[*] Compiling TypeScript..." -ForegroundColor Blue
npx tsc -p tsconfig.json --noEmit false
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "[!] TypeScript compilation failed." -ForegroundColor Red
    exit 1
}
Write-Host "[+] TypeScript compiled." -ForegroundColor Green
Write-Host ""

# Package extension — vsce writes <name>-<version>.vsix into dist/
# --allow-missing-license: the project LICENSE lives at the repo root, not in src/vscode/
Write-Host "[*] Packaging extension with vsce..." -ForegroundColor Blue
npx @vscode/vsce package --no-dependencies --allow-missing-license --out "$projectRoot\dist"
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "[!] vsce package failed." -ForegroundColor Red
    exit 1
}
Pop-Location

$vsix = Get-ChildItem "$projectRoot\dist" -Filter "*.vsix" |
Sort-Object LastWriteTime -Descending |
Select-Object -First 1

if (-not $vsix) {
    Write-Host "[!] No .vsix found in dist/ after packaging." -ForegroundColor Red
    exit 1
}

Write-Host "[+] Packaged: $($vsix.Name)" -ForegroundColor Green
Write-Host ""

# Install into VS Code
if (Get-Command code -ErrorAction SilentlyContinue) {
    Write-Host "[*] Installing extension into VS Code..." -ForegroundColor Blue
    code --install-extension $vsix.FullName --force
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] VS Code extension install failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "[+] Extension installed." -ForegroundColor Green
}
else {
    Write-Host "[~] 'code' CLI not found — skipping VS Code install." -ForegroundColor Yellow
    Write-Host "    Install manually: code --install-extension $($vsix.FullName)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[+] ==========================================" -ForegroundColor Cyan
Write-Host "[+] VS Code extension ready: $($vsix.Name)" -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Cyan
