<#
.SYNOPSIS
    Setup script for the XYZ Platform on Windows.
.DESCRIPTION
    Installs uv, creates a virtual environment, and installs project dependencies.
.EXAMPLE
    .\Setup.ps1
.NOTES
#>

# Function to get the project root directory
function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] XYZ Platform - Setup" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

# Check execution policy
$executionPolicy = Get-ExecutionPolicy -Scope CurrentUser
if ($executionPolicy -eq "Restricted") {
    Write-Host "[*] Setting execution policy to RemoteSigned..." -ForegroundColor Yellow
    try {
        Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
        Write-Host "[+] Execution policy updated." -ForegroundColor Green
    }
    catch {
        Write-Host "[!] Failed to update execution policy. Run as administrator if needed." -ForegroundColor Red
    }
    Write-Host ""
}

# Install uv if not already available
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[*] Installing uv..." -ForegroundColor Blue
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # Refresh PATH for current session
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH
    Write-Host "[+] uv installed." -ForegroundColor Green
    Write-Host ""
}
else {
    Write-Host "[+] uv already available: $(uv --version)" -ForegroundColor Green
    Write-Host ""
}

# Create virtual environment
Write-Host "[*] Creating virtual environment with uv..." -ForegroundColor Blue
uv venv "$projectRoot\.venv"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Failed to create virtual environment." -ForegroundColor Red
    Read-Host "[.] Press Enter to exit"
    exit 1
}
Write-Host "[+] Virtual environment created." -ForegroundColor Green
Write-Host ""

# Activate virtual environment
Write-Host "[*] Activating virtual environment..." -ForegroundColor Blue
& "$projectRoot\.venv\Scripts\Activate.ps1"
Write-Host "[+] Virtual environment activated." -ForegroundColor Green
Write-Host ""

# Install project dependencies
Write-Host "[*] Installing dependencies..." -ForegroundColor Blue
uv pip install -e .
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Failed to install dependencies." -ForegroundColor Red
    Read-Host "[.] Press Enter to exit"
    exit 1
}
Write-Host "[+] Dependencies installed." -ForegroundColor Green
Write-Host ""

Write-Host "[+] ================================================" -ForegroundColor Cyan
Write-Host "[+] Setup completed successfully!" -ForegroundColor Green
Write-Host "[+] ================================================" -ForegroundColor Cyan

Read-Host "[.] Press Enter to continue"
