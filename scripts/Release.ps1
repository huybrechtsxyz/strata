<#
.SYNOPSIS
    Bumps VERSION.txt, commits, and creates a git tag for release.
.DESCRIPTION
    Updates VERSION.txt to the specified version, commits the change,
    and creates a git tag. Push manually to trigger CI release pipeline.
.PARAMETER Version
    The version number to release (e.g. 0.0.2). Must be in X.Y.Z format.
.EXAMPLE
    .\scripts\Release.ps1 -Version "0.0.2"
    git push origin main --tags
.NOTES
    The push is intentionally separate — review before triggering CI.
#>

param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version
)

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

$tag = "v$Version"
$versionFile = Join-Path $projectRoot "VERSION.txt"

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] XYZ Platform - Release $tag" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

# Abort if tag already exists
$existingTag = git tag --list $tag
if ($existingTag) {
    Write-Host "[!] Tag $tag already exists. Aborting." -ForegroundColor Red
    exit 1
}

# Abort if working tree is dirty (uncommitted changes other than VERSION.txt)
$dirty = git status --porcelain | Where-Object { $_ -notmatch '^\s*M\s+VERSION\.txt' }
if ($dirty) {
    Write-Host "[!] Working tree has uncommitted changes. Commit or stash them first." -ForegroundColor Red
    git status --short
    exit 1
}

# 1. Write new version
$current = Get-Content $versionFile -Raw
Write-Host "[*] VERSION.txt: $($current.Trim()) → $Version" -ForegroundColor Yellow
Set-Content -Path $versionFile -Value $Version -NoNewline

# 2. Stage and commit
git add VERSION.txt
git commit -m "chore: release $tag"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] git commit failed." -ForegroundColor Red
    exit 1
}

# 3. Create annotated tag
git tag -a $tag -m "Release $tag"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] git tag failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[+] Version bumped and tagged: $tag" -ForegroundColor Green
Write-Host "[+] Run the following to trigger the CI release pipeline:" -ForegroundColor Green
Write-Host ""
Write-Host "    git push origin main --tags" -ForegroundColor White
Write-Host ""
