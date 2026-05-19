<#
.SYNOPSIS
    Rename layer 3: CLI name, env vars, workspace marker across all file types.
.DESCRIPTION
    Applies the following renames across src/, tests/, docs/, scripts/, .github/ and root files:
    1. xyz_platform in non-py files (yml, md, sh...) -> strata
    2. XYZ_<VAR>  ->  STRATA_<VAR>  (uppercase env vars only)
    3. .platform/ and ".platform"  ->  .strata/ and ".strata"
    4. .xyz-platform (legacy alt state dir)  ->  .strata
    5. xyz-platform (CLI/package name)  ->  strata
       (GitHub repo URLs preserved via negative lookbehind)
.NOTES
    Run from the repo root after Rename-Layer2.ps1.
    Skips: scripts/Rename-Layer*.ps1, config/ (user data), .git/
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[*] Layer 3 rename: CLI name, env vars, workspace marker" -ForegroundColor Cyan
Write-Host "[*] Root: $root" -ForegroundColor Cyan
Write-Host ""

$totalFiles = 0

function Update-File {
    param([System.IO.FileInfo]$File)

    $original = Get-Content $File -Raw
    if ($null -eq $original) { return }
    $updated = $original

    # 1. xyz_platform in non-Python files (CONTRIBUTING, action.yml, SQUAD.md etc.)
    #    Python files were done in layer 2; this catches yml/md/sh/yaml/json
    $updated = $updated -replace 'from xyz_platform\.', 'from strata.'
    $updated = $updated -replace 'import xyz_platform\.', 'import strata.'
    $updated = $updated -replace '--cov=xyz_platform\b', '--cov=strata'
    $updated = $updated -replace 'tests/xyz_platform/', 'tests/strata/'
    $updated = $updated -replace '(?<!\w)-m xyz_platform\b', '-m strata'
    # bare xyz_platform word (not part of a longer identifier or URL path)
    $updated = $updated -replace '(?<![/\w])xyz_platform(?![/\w])', 'strata'

    # 2. Env vars: XYZ_<UPPERCASE> -> STRATA_<UPPERCASE>
    $updated = $updated -replace '\bXYZ_([A-Z][A-Z0-9_]*)\b', 'STRATA_$1'

    # 3a. .platform/ -> .strata/  (slash form — paths and docs)
    $updated = $updated -replace '(?<!\w)\.platform/', '.strata/'

    # 3b. ".platform" and '.platform' (Python Path joins without trailing slash)
    # (?-i) makes this case-sensitive — prevents matching .PLATFORM_MODEL or .platform_builder
    # Only matches .platform when followed by quote, space, backslash, or end of string
    $updated = $updated -replace '(?-i)\.platform(?=["''\\\s]|$)', '.strata'
    $updated = $updated -replace '"\. platform"', '".strata"'

    # 3c. Backslash form in Windows paths: \.platform\
    $updated = $updated -replace '\\\.platform\\', '\.strata\'

    # 4. .xyz-platform (legacy alt state dir) -> .strata
    $updated = $updated -replace '\.xyz-platform(?=[/"''`\s]|$)', '.strata'

    # 5. xyz-platform -> strata  (CLI/package/product name)
    #    Preserve GitHub repo URLs: negative lookbehind on "huybrechtsxyz/" and "github.com/"
    #    Also preserve: xyz-platform-docs (handled separately below as strata-docs)
    $updated = $updated -replace '(?<!huybrechtsxyz/)xyz-platform-docs', 'strata-docs'
    $updated = $updated -replace '(?<!huybrechtsxyz/)xyz-platform', 'strata'

    if ($updated -ne $original) {
        Set-Content $File $updated -NoNewline
        $relativePath = $File.FullName.Replace($root + "\", "")
        Write-Host "[+] $relativePath" -ForegroundColor Green
        $script:totalFiles++
    }
}

# Collect files from src/, tests/, docs/, scripts/ (exclude Rename-Layer*.ps1)
$extensions = @("*.py","*.yaml","*.yml","*.ps1","*.rst","*.md","*.json","*.sh","*.txt","*.toml")

$files = @(
    Get-ChildItem -Recurse -File -Include $extensions -Path (Join-Path $root "src")
    Get-ChildItem -Recurse -File -Include $extensions -Path (Join-Path $root "tests")
    Get-ChildItem -Recurse -File -Include $extensions -Path (Join-Path $root "docs")
    Get-ChildItem -Recurse -File -Include $extensions -Path (Join-Path $root ".github")
    Get-ChildItem -Recurse -File -Include $extensions -Path (Join-Path $root "scripts") |
        Where-Object { $_.Name -notmatch "^Rename-Layer" }
    # Root-level files
    Get-ChildItem -File -Include $extensions -Path $root |
        Where-Object { $_.Name -in @("README.md","CHANGELOG.md","package.json","noxfile.py","pyproject.toml") }
) | Where-Object { $_ -is [System.IO.FileInfo] }

Write-Host "[*] Processing $($files.Count) files..." -ForegroundColor Blue
Write-Host ""

foreach ($file in $files) {
    Update-File -File $file
}

Write-Host ""
Write-Host "[+] ==========================================" -ForegroundColor Green
Write-Host "[+] Layer 3 complete: $totalFiles file(s) updated." -ForegroundColor Green
Write-Host "[+] Next: run tests, then layer 4 (docs/scripts final pass + README)." -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Green
