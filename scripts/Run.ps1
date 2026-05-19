<#
.SYNOPSIS
    Runs the XYZ Platform CLI, forwarding all arguments.
.DESCRIPTION
    Executes the strata CLI via uv. All arguments are forwarded to the CLI.
.PARAMETER Clean
    Removes all __pycache__ directories before running.
.EXAMPLE
    .\Run.ps1 version
    .\Run.ps1 deploy platform.yaml --log-level debug
    .\Run.ps1 -Clean deploy platform.yaml
.NOTES
#>

param(
    [switch]$Clean,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

if ($Clean) {
    Write-Host "[*] Cleaning __pycache__ directories..." -ForegroundColor Yellow
    Get-ChildItem -Path $projectRoot -Recurse -Directory -Filter "__pycache__" |
    ForEach-Object {
        Write-Host "    Removing: $($_.FullName)" -ForegroundColor Gray
        Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[+] Cache cleanup completed." -ForegroundColor Green
    Write-Host ""
}

# Run via uv so it always uses the correct venv and PYTHONPATH
uv run strata @RemainingArgs
