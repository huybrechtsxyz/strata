<#
.SYNOPSIS
Runs the xyz_platform CLI from the src directory, forwarding all arguments.

.DESCRIPTION
This script changes to the src directory and executes the xyz_platform CLI using the project's virtual environment Python interpreter. All arguments passed to this script are forwarded to the CLI.

.PARAMETER clean
Cleans all __pycache__ directories before running the CLI.

.EXAMPLE
.\run.ps1 version

.EXAMPLE
.\run.ps1 deploy clean platform.yaml --log-level debug

.EXAMPLE
.\run.ps1 --clean deploy clean platform.yaml
#>

param(
    [switch]$clean,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

# Get the project root directory (parent of scripts folder)
function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

$ProjectRoot = Get-ProjectRoot

# Check if --clean option is provided
if ($clean) {
    Write-Host "Cleaning __pycache__ directories..." -ForegroundColor Yellow
    
    # Remove all __pycache__ directories recursively
    Get-ChildItem -Path $ProjectRoot -Recurse -Directory -Filter "__pycache__" | 
    ForEach-Object { 
        Write-Host "Removing: $($_.FullName)" -ForegroundColor Gray
        Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    
    Write-Host "Cache cleanup completed." -ForegroundColor Green
}

# Set PYTHONPATH to include src directory
$env:PYTHONPATH = "$ProjectRoot\src"

# Build the command arguments as a single array
$pythonArgs = @("-m", "xyz_platform.cli") + $RemainingArgs

# Run the CLI with all arguments properly passed
& "$ProjectRoot\.venv\Scripts\python.exe" @pythonArgs
