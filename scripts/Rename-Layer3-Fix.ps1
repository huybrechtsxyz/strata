<#
.SYNOPSIS
    Fix over-renames from Layer 3: reverse wrongly renamed module paths and enum values.
.DESCRIPTION
    The .platform regex in Layer 3 was case-insensitive and matched Python module
    dotted paths (.platform_builder, .PLATFORM_MODEL, etc.). This script reverses those.
.NOTES
    Run once after Rename-Layer3.ps1. Safe to re-run.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[*] Layer 3 fix: reverse over-renamed module paths and enum values" -ForegroundColor Cyan
Write-Host ""

$totalFiles = 0

$files = Get-ChildItem -Recurse -File -Include "*.py" -Path (Join-Path $root "src"),(Join-Path $root "tests")

foreach ($file in $files) {
    $original = Get-Content $file -Raw
    if ($null -eq $original) { continue }
    $updated = $original

    # Reverse wrongly renamed module paths in dotted Python paths
    # These are module/file names that legitimately keep "platform_" prefix
    $updated = $updated -replace '(?-i)\.strata_builder\b',         '.platform_builder'
    $updated = $updated -replace '(?-i)\.strata_validator\b',       '.platform_validator'
    $updated = $updated -replace '(?-i)\.strata_artifact_model\b',  '.platform_artifact_model'
    $updated = $updated -replace '(?-i)\.strata_artifact_service\b','.platform_artifact_service'
    $updated = $updated -replace '(?-i)\.strata_template_model\b',  '.platform_template_model'
    $updated = $updated -replace '(?-i)\.strata_template_service\b','.platform_template_service'

    # Reverse wrongly renamed enum value: PlatformKind.strata_MODEL -> PlatformKind.PLATFORM_MODEL
    $updated = $updated -replace '(?-i)PlatformKind\.strata_MODEL\b', 'PlatformKind.PLATFORM_MODEL'

    # Reverse stdlib attribute: sys.strata -> sys.platform
    $updated = $updated -replace '(?-i)\bsys\.strata\b', 'sys.platform'

    if ($updated -ne $original) {
        Set-Content $file $updated -NoNewline
        $relativePath = $file.FullName.Replace($root + "\", "")
        Write-Host "[+] $relativePath" -ForegroundColor Green
        $script:totalFiles++
    }
}

Write-Host ""
Write-Host "[+] ==========================================" -ForegroundColor Green
Write-Host "[+] Fix complete: $totalFiles file(s) corrected." -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Green
