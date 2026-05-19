<#
.SYNOPSIS
    Rename layer 2: Python imports and string references in all .py files.
.DESCRIPTION
    Replaces all occurrences of xyz_platform in Python files:
    - from xyz_platform.  ->  from strata.
    - import xyz_platform. ->  import strata.
    - "xyz_platform.      ->  "strata.       (patch() strings in tests)
    - project = "xyz_platform"  ->  project = "strata"  (docs/conf.py)
.NOTES
    Run from the repo root after Rename-Layer1.ps1.
    Safe to re-run: replacements are idempotent once done.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[*] Layer 2 rename: Python imports xyz_platform -> strata" -ForegroundColor Cyan
Write-Host "[*] Root: $root" -ForegroundColor Cyan
Write-Host ""

$totalFiles = 0
$totalReplacements = 0

# Collect all .py files under src/, tests/, and specific root files
$files = @(
    Get-ChildItem -Recurse -File -Include "*.py" -Path (Join-Path $root "src")
    Get-ChildItem -Recurse -File -Include "*.py" -Path (Join-Path $root "tests")
    Join-Path $root "noxfile.py"
    Join-Path $root "docs\conf.py"
) | Where-Object { Test-Path $_ }

Write-Host "[*] Processing $($files.Count) Python files..." -ForegroundColor Blue
Write-Host ""

foreach ($file in $files) {
    $original = Get-Content $file -Raw
    $updated = $original

    # from xyz_platform. -> from strata.
    $updated = $updated -replace 'from xyz_platform\.', 'from strata.'

    # import xyz_platform. -> import strata.
    $updated = $updated -replace 'import xyz_platform\.', 'import strata.'

    # bare: import xyz_platform (without dot) -> import strata
    $updated = $updated -replace '(?<!["\w])import xyz_platform(?![\.\w])', 'import strata'

    # patch() and similar string refs: "xyz_platform. -> "strata.
    $updated = $updated -replace '"xyz_platform\.', '"strata.'

    # logger key in YAML snippets / config dicts: xyz_platform -> strata
    $updated = $updated -replace '(?<=loggers[^"]*["\s])xyz_platform(?=["\s])', 'strata'
    $updated = $updated -replace '"loggers\.xyz_platform\.', '"loggers.strata.'
    $updated = $updated -replace "'loggers\.xyz_platform\.", "'loggers.strata."

    # VS Code launch.json paths embedded in solution_controller
    $updated = $updated -replace 'src/xyz_platform/__main__\.py', 'src/strata/__main__.py'

    # __main__.py module docstring / comment: python -m xyz_platform
    $updated = $updated -replace '\bxyz_platform\b(?=\s*\)\.|\s*as\s+module)', 'strata'
    $updated = $updated -replace '\(python -m xyz_platform\)', '(python -m strata)'
    $updated = $updated -replace 'running xyz_platform as a module', 'running strata as a module'

    # docs/conf.py project name
    $updated = $updated -replace 'project = "xyz_platform"', 'project = "strata"'

    # docstrings and comments referencing the module by name (e.g. "Tests for xyz_platform.X")
    $updated = $updated -replace '(?<=Tests for |for )xyz_platform\.', 'strata.'

    # bare xyz_platform in test: xyz_platform.__version__
    $updated = $updated -replace '\bxyz_platform\.__version__', 'strata.__version__'

    # remaining bare logger name string "xyz_platform"
    $updated = $updated -replace '"xyz_platform"', '"strata"'

    # YAML-in-string logger key: \n  xyz_platform:\n  (test fixtures)
    $updated = $updated -replace '(loggers:\\n\s+)xyz_platform(:)', '$1strata$2'

    # RST/docstring inline code: ``xyz_platform``
    $updated = $updated -replace '``xyz_platform``', '``strata``'

    if ($updated -ne $original) {
        Set-Content $file $updated -NoNewline
        $relativePath = $file.FullName.Replace($root + "\", "")
        Write-Host "[+] $relativePath" -ForegroundColor Green
        $totalFiles++
    }
}

Write-Host ""
Write-Host "[+] ==========================================" -ForegroundColor Green
Write-Host "[+] Layer 2 complete: $totalFiles file(s) updated." -ForegroundColor Green
Write-Host "[+] Next step: run Rename-Layer3.ps1 for CLI name + env vars." -ForegroundColor Green
Write-Host "[+] ==========================================" -ForegroundColor Green
