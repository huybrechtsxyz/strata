<#
.SYNOPSIS
    Setup script for the XYZ Platform virtual environment on Windows.
.DESCRIPTION
    This script sets up a Python virtual environment for the XYZ Platform project on Windows.
    It checks for Python installation, creates and activates a virtual environment,
    installs required dependencies, and optionally installs the Bitwarden Secrets Manager CLI
    and Terraform CLI tools.
.PARAMETER PyVersion
    Specifies the Python version to set in configuration files (default is "3.9").
.PARAMETER Bitwarden
    Switch to indicate whether to install the Bitwarden Secrets Manager CLI.
.PARAMETER Terraform
    Switch to indicate whether to install the Terraform CLI.
.EXAMPLE
    .\Setup.ps1 -PyVersion "3.10" -Bitwarden -Terraform
    Sets up the virtual environment with Python 3.10 and installs both Bitwarden and Terraform CLIs.
.NOTES
#>

param(
    [switch]$Bitwarden,
    [switch]$Terraform
)

# Function to get the project root directory
function Get-ProjectRoot {
    return Split-Path -Parent $PSScriptRoot
}

# Function to get default Python version from pyproject.toml
function Get-DefaultPythonVersion {
    $projectRoot = Get-ProjectRoot
    $defaultVersion = "3.11"
    $tomlPath = "$projectRoot\pyproject.toml"
    
    if (Test-Path $tomlPath) {
        $content = Get-Content $tomlPath -Raw -ErrorAction SilentlyContinue
        # Match requires-python = ">=3.11" or similar patterns
        if ($content -match 'requires-python\s*=\s*">=?(\d+\.\d+)') {
            $defaultVersion = $matches[1]
        }
    }
    return $defaultVersion
}

# Function to find Python installation (returns highest version available)
function Find-Python {
    Write-Host "[*] Searching for Python installations..." -ForegroundColor Blue
    
    $pythonCandidates = @()
    
    # 1. Check common command names in PATH
    $pythonCommands = @('python', 'python3', 'py')
    foreach ($cmd in $pythonCommands) {
        $pythonCmd = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $pythonCandidates += @{
                Path    = $pythonCmd.Source
                Command = $cmd
            }
        }
    }
    
    # 2. Search common installation paths
    $pythonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "$env:PROGRAMFILES\Python*\python.exe",
        "$env:PROGRAMFILES(X86)\Python*\python.exe",
        "$env:APPDATA\Local\Programs\Python\Python*\python.exe",
        "$env:USERPROFILE\AppData\Local\Microsoft\WindowsApps\python.exe"
    )
    
    $foundPaths = $pythonPaths | ForEach-Object { 
        Get-ChildItem $_ -ErrorAction SilentlyContinue 
    }
    
    foreach ($path in $foundPaths) {
        $pythonCandidates += @{
            Path    = $path.FullName
            Command = $path.FullName
        }
    }
    
    # 3. Try registry search
    try {
        $regPaths = Get-ChildItem "HKLM:\SOFTWARE\Python\PythonCore" -ErrorAction SilentlyContinue |
        ForEach-Object { 
            $installPath = Get-ItemProperty "$($_.PSPath)\InstallPath" -ErrorAction SilentlyContinue
            if ($installPath -and $installPath.'(default)') {
                $exePath = Join-Path $installPath.'(default)' "python.exe"
                if (Test-Path $exePath) {
                    $exePath
                }
            }
        }
        
        foreach ($path in $regPaths) {
            $pythonCandidates += @{
                Path    = $path
                Command = $path
            }
        }
    }
    catch {
        # Registry search failed, continue
    }
    
    # Remove duplicates (same path)
    $uniqueCandidates = $pythonCandidates | Sort-Object -Property Path -Unique
    
    if ($uniqueCandidates.Count -eq 0) {
        Write-Host "[!] Python installation not found!" -ForegroundColor Red
        Write-Host "[!] Please install Python from: https://python.org/downloads" -ForegroundColor Red
        Write-Host "[!] Or ensure Python is added to your PATH environment variable." -ForegroundColor Red
        return $null
    }
    
    Write-Host "[*] Found $($uniqueCandidates.Count) Python installation(s), checking versions..." -ForegroundColor Blue
    
    # Query version for each candidate
    $pythonVersions = @()
    foreach ($candidate in $uniqueCandidates) {
        try {
            $versionOutput = & $candidate.Command --version 2>&1
            # Match Python X.Y or Python X.Y.Z (with optional pre-release suffixes like rc1, a1, etc.)
            if ($versionOutput -match 'Python\s+(\d+)\.(\d+)(?:\.(\d+))?') {
                $major = [int]$matches[1]
                $minor = [int]$matches[2]
                # Patch version is optional, default to 0 if not present
                $patch = if ($matches[3]) { [int]$matches[3] } else { 0 }
                
                $pythonVersions += [PSCustomObject]@{
                    Path          = $candidate.Path
                    Command       = $candidate.Command
                    VersionString = $versionOutput.ToString().Trim()
                    Major         = $major
                    Minor         = $minor
                    Patch         = $patch
                }
                
                Write-Host "  [+] $($candidate.Path): $versionOutput" -ForegroundColor Gray
            }
            else {
                Write-Host "  [!] Could not parse version for: $($candidate.Path) (output: $versionOutput)" -ForegroundColor DarkGray
            }
        }
        catch {
            # Skip this candidate if version check fails
            Write-Host "  [!] Failed to check version for: $($candidate.Path) - $($_.Exception.Message)" -ForegroundColor DarkGray
        }
    }
    
    if ($pythonVersions.Count -eq 0) {
        Write-Host "[!] No valid Python installations found!" -ForegroundColor Red
        return $null
    }
    
    # Sort by version (Major.Minor.Patch) descending and pick the highest
    $highestVersion = $pythonVersions | Sort-Object -Property Major, Minor, Patch -Descending | Select-Object -First 1
    
    Write-Host "[+] Selected highest version: $($highestVersion.VersionString)" -ForegroundColor Green
    Write-Host "[+] Location: $($highestVersion.Path)" -ForegroundColor Green
    
    return $highestVersion.Command
}

# Function to install Bitwarden Secrets Manager CLI
function Install-BitwardenCLI {
    Write-Host "[*] Installing Bitwarden Secrets Manager CLI..." -ForegroundColor Blue
    
    $projectRoot = Get-ProjectRoot
    $toolsDir = "$projectRoot\.venv\tools"
    $bwsDir = "$toolsDir\bws"
    $bwsExe = "$bwsDir\bws.exe"
    
    # Check if already installed
    if (Test-Path $bwsExe) {
        Write-Host "[+] Bitwarden CLI already installed at: $bwsExe" -ForegroundColor Green
        return $true
    }
    
    # Create tools directory
    if (-not (Test-Path $toolsDir)) {
        New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    }
    
    if (-not (Test-Path $bwsDir)) {
        New-Item -ItemType Directory -Path $bwsDir -Force | Out-Null
    }
    
    try {
        # Get the latest release info from GitHub API
        Write-Host "[*] Fetching latest Bitwarden CLI release information..." -ForegroundColor Blue
        
        # Try both possible repositories
        $repositories = @(
            "bitwarden/sdk",
            "bitwarden/sdk-sm",
            "bitwarden/cli"
        )
        
        $downloadUrl = $null
        $fileName = $null
        
        foreach ($repo in $repositories) {
            try {
                $apiUrl = "https://api.github.com/repos/$repo/releases/latest"
                $release = Invoke-RestMethod -Uri $apiUrl -Headers @{"User-Agent" = "xyz-platform-setup" } -ErrorAction SilentlyContinue
                
                # Look for Windows assets with different naming patterns
                $patterns = @("*windows*x64*.zip", "*win*x64*.zip", "*windows*.zip", "*win*.exe", "*bws*windows*.zip", "*bws*.exe")
                
                foreach ($pattern in $patterns) {
                    $asset = $release.assets | Where-Object { $_.name -like $pattern } | Select-Object -First 1
                    if ($asset) {
                        $downloadUrl = $asset.browser_download_url
                        $fileName = $asset.name
                        Write-Host "[+] Found asset in $repo`: $fileName" -ForegroundColor Green
                        break
                    }
                }
                
                if ($downloadUrl) { break }
            }
            catch {
                Write-Host "[*] Repository $repo not accessible or no suitable releases" -ForegroundColor Gray
                continue
            }
        }
        
        # Fallback to direct download if GitHub API fails
        if (-not $downloadUrl) {
            Write-Host "[*] GitHub API search failed, trying direct download..." -ForegroundColor Yellow
            
            # Try known working URLs
            $directUrls = @(
                @{
                    url  = "https://github.com/bitwarden/sdk/releases/latest/download/bws-x86_64-pc-windows-msvc.zip"
                    name = "bws-x86_64-pc-windows-msvc.zip"
                },
                @{
                    url  = "https://github.com/bitwarden/cli/releases/latest/download/bw-windows-x64.zip"
                    name = "bw-windows-x64.zip"
                }
            )
            
            foreach ($urlInfo in $directUrls) {
                try {
                    # Test if URL exists
                    $response = Invoke-WebRequest -Uri $urlInfo.url -Method Head -ErrorAction SilentlyContinue
                    if ($response.StatusCode -eq 200) {
                        $downloadUrl = $urlInfo.url
                        $fileName = $urlInfo.name
                        Write-Host "[+] Using direct download: $fileName" -ForegroundColor Green
                        break
                    }
                }
                catch {
                    continue
                }
            }
        }
        
        if (-not $downloadUrl) {
            Write-Host "[!] Could not find a suitable Bitwarden CLI release for Windows x64" -ForegroundColor Red
            Write-Host "[!] Please manually download from: https://github.com/bitwarden/sdk/releases" -ForegroundColor Yellow
            return $false
        }
        
        $zipPath = "$bwsDir\$fileName"
        
        Write-Host "[*] Downloading Bitwarden CLI: $fileName" -ForegroundColor Blue
        Write-Host "[*] URL: $downloadUrl" -ForegroundColor Gray
        
        # Download the release
        Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath -ErrorAction Stop
        
        Write-Host "[+] Download completed: $zipPath" -ForegroundColor Green
        
        # Extract the ZIP file or handle EXE
        if ($fileName -like "*.zip") {
            Write-Host "[*] Extracting Bitwarden CLI..." -ForegroundColor Blue
            Expand-Archive -Path $zipPath -DestinationPath $bwsDir -Force
            
            # Clean up ZIP file
            Remove-Item $zipPath -Force
            
            # Find the executable (could be bws.exe or bw.exe)
            $exeFiles = Get-ChildItem "$bwsDir\*.exe" -Recurse
            if ($exeFiles) {
                $actualExe = $exeFiles[0].FullName
                # If it's not named bws.exe, copy/rename it
                if ($actualExe -ne $bwsExe) {
                    Copy-Item $actualExe $bwsExe -Force
                    Write-Host "[*] Renamed executable to: bws.exe" -ForegroundColor Blue
                }
            }
        }
        else {
            # It's an EXE file, just rename it
            Move-Item $zipPath $bwsExe -Force
        }
        
        # Verify installation
        if (Test-Path $bwsExe) {
            Write-Host "[+] Bitwarden CLI installed successfully!" -ForegroundColor Green
            
            # Test the CLI
            try {
                $version = & $bwsExe --version 2>&1
                Write-Host "[+] Bitwarden CLI version: $version" -ForegroundColor Green
            }
            catch {
                Write-Host "[!] Warning: Could not verify Bitwarden CLI version" -ForegroundColor Yellow
            }
            
            # Add to PATH for this session
            $currentPath = $env:PATH
            $bwsPath = Resolve-Path $bwsDir
            if ($currentPath -notlike "*$bwsPath*") {
                $env:PATH = "$bwsPath;$currentPath"
                Write-Host "[+] Added Bitwarden CLI to PATH for this session" -ForegroundColor Green
            }
            
            Write-Host "[*] Bitwarden CLI location: $bwsExe" -ForegroundColor Cyan
            Write-Host "[*] To use in future sessions, add to PATH: $bwsPath" -ForegroundColor Cyan
            Write-Host ""
            
            return $true
        }
        else {
            Write-Host "[!] Installation verification failed - bws.exe not found" -ForegroundColor Red
            return $false
        }
        
    }
    catch {
        Write-Host "[!] Failed to install Bitwarden CLI: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

# Function to install Terraform CLI
function Install-TerraformCLI {
    Write-Host "[*] Installing Terraform CLI..." -ForegroundColor Blue
    
    $projectRoot = Get-ProjectRoot
    $toolsDir = "$projectRoot\.venv\tools"
    $terraformDir = "$toolsDir\terraform"
    $terraformExe = "$terraformDir\terraform.exe"
    
    # Check if already installed
    if (Test-Path $terraformExe) {
        Write-Host "[+] Terraform CLI already installed at: $terraformExe" -ForegroundColor Green
        return $true
    }
    
    # Create tools directory
    if (-not (Test-Path $toolsDir)) {
        New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    }
    
    if (-not (Test-Path $terraformDir)) {
        New-Item -ItemType Directory -Path $terraformDir -Force | Out-Null
    }
    
    try {
        # Get the latest release info from HashiCorp releases API
        Write-Host "[*] Fetching latest Terraform release information..." -ForegroundColor Blue
        
        $apiUrl = "https://api.releases.hashicorp.com/v1/releases/terraform?limit=1"
        $release = Invoke-RestMethod -Uri $apiUrl -Headers @{"User-Agent" = "xyz-platform-setup" } -ErrorAction Stop
        
        if (-not $release -or $release.Count -eq 0) {
            Write-Host "[!] Could not fetch Terraform release information" -ForegroundColor Red
            return $false
        }
        
        $version = $release[0].version
        $builds = $release[0].builds
        
        # Find the Windows AMD64 build
        $windowsBuild = $builds | Where-Object { $_.os -eq "windows" -and $_.arch -eq "amd64" } | Select-Object -First 1
        
        if (-not $windowsBuild) {
            Write-Host "[!] Could not find Windows AMD64 build for Terraform" -ForegroundColor Red
            return $false
        }
        
        $downloadUrl = $windowsBuild.url
        $fileName = "terraform_$($version)_windows_amd64.zip"
        $zipPath = "$terraformDir\$fileName"
        
        Write-Host "[*] Downloading Terraform CLI: v$version" -ForegroundColor Blue
        Write-Host "[*] URL: $downloadUrl" -ForegroundColor Gray
        
        # Download the release
        Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath -ErrorAction Stop
        
        Write-Host "[+] Download completed: $zipPath" -ForegroundColor Green
        
        # Extract the ZIP file
        Write-Host "[*] Extracting Terraform CLI..." -ForegroundColor Blue
        Expand-Archive -Path $zipPath -DestinationPath $terraformDir -Force
        
        # Clean up ZIP file
        Remove-Item $zipPath -Force
        
        # Verify installation
        if (Test-Path $terraformExe) {
            Write-Host "[+] Terraform CLI installed successfully!" -ForegroundColor Green
            
            # Test the CLI
            try {
                $version = & $terraformExe version 2>&1
                $versionLine = ($version -split "`n")[0]
                Write-Host "[+] Terraform CLI version: $versionLine" -ForegroundColor Green
            }
            catch {
                Write-Host "[!] Warning: Could not verify Terraform CLI version" -ForegroundColor Yellow
            }
            
            # Add to PATH for this session
            $currentPath = $env:PATH
            $terraformPath = Resolve-Path $terraformDir
            if ($currentPath -notlike "*$terraformPath*") {
                $env:PATH = "$terraformPath;$currentPath"
                Write-Host "[+] Added Terraform CLI to PATH for this session" -ForegroundColor Green
            }
            
            Write-Host "[*] Terraform CLI location: $terraformExe" -ForegroundColor Cyan
            Write-Host "[*] To use in future sessions, add to PATH: $terraformPath" -ForegroundColor Cyan
            Write-Host ""
            
            return $true
        }
        else {
            Write-Host "[!] Installation verification failed - terraform.exe not found" -ForegroundColor Red
            return $false
        }
        
    }
    catch {
        Write-Host "[!] Failed to install Terraform CLI: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

# Function to update python version in pyproject.toml
function Update-PythonVersionInPyproject {
    param (
        [string]$FilePath = "$(Get-ProjectRoot)\pyproject.toml",
        [string]$NewVersion
    )
    if (Test-Path $FilePath) {
        $content = Get-Content $FilePath
        $updatedContent = $content -replace 'requires-python\s*=\s*".*"', "requires-python = `">=$NewVersion`""
        Set-Content -Path $FilePath -Value $updatedContent
        Write-Host "[*] Updated Python version in pyproject.toml to >= $NewVersion" -ForegroundColor Green
    }
    else {
        Write-Host "[!] pyproject.toml not found at $FilePath" -ForegroundColor Red
    }
}

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] XYZ Platform - Virtual Environment Setup" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

# Check execution policy for activation
$executionPolicy = Get-ExecutionPolicy -Scope CurrentUser
if ($executionPolicy -eq "Restricted") {
    Write-Host "PowerShell execution policy is restricted. Setting to RemoteSigned..." -ForegroundColor Yellow
    try {
        Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
        Write-Host "[*] Execution policy updated." -ForegroundColor Green
    }
    catch {
        Write-Host "[!] Failed to update execution policy. You may need to run as administrator." -ForegroundColor Red
        Write-Host "[!] Or manually run: Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser" -ForegroundColor Yellow
    }
    Write-Host ""
}

# Determine Python version to use
$PyVersion = Get-DefaultPythonVersion
Write-Host "[*] Using Python version: $PyVersion" -ForegroundColor Blue
Write-Host ""

# Install the official Microsoft Pester module if not already installed
# Install-Module -Name Pester -Scope CurrentUser -Force -Repository PSGallery -AllowClobber -SkipPublisherCheck

# Install the powershell-yaml module if not already installed
# Install-Module -Name powershell-yaml -Scope CurrentUser -Force

# Update python version in pyproject.toml
Update-PythonVersionInPyproject -NewVersion $PyVersion

# Find Python executable
$pythonExe = Find-Python
if (-not $pythonExe) {
    Read-Host "[.] Press Enter to exit"
    exit 1
}

# Test Python installation
Write-Host "[*] Testing Python installation..." -ForegroundColor Blue
try {
    $pythonVersion = & $pythonExe --version 2>&1
    Write-Host "[+] Python version: $pythonVersion" -ForegroundColor Green
}
catch {
    Write-Host "[!] Failed to execute Python. Installation may be corrupted." -ForegroundColor Red
    Read-Host "[.] Press Enter to exit"
    exit 1
}
Write-Host ""

# Get project root and change to it
$projectRoot = Get-ProjectRoot
Set-Location $projectRoot

# Check if .venv already exists
if (Test-Path "$projectRoot\.venv") {
    Write-Host "[*] Virtual environment already exists." -ForegroundColor Yellow
    Write-Host ""
}
else {
    # Create virtual environment
    Write-Host "[*] Creating virtual environment..." -ForegroundColor Green
    & $pythonExe -m venv "$projectRoot\.venv"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] Failed to create virtual environment." -ForegroundColor Red
        Write-Host "[!] Please ensure Python venv module is available." -ForegroundColor Red
        Write-Host "[!] Try: $pythonExe -m pip install --user virtualenv" -ForegroundColor Yellow
        Read-Host "[.] Press Enter to exit"
        exit 1
    }
    Write-Host "[+] Virtual environment created successfully." -ForegroundColor Green
    Write-Host ""
}

# Activate virtual environment
Write-Host "[*] Activating virtual environment..." -ForegroundColor Green
try {
    & "$projectRoot\.venv\Scripts\Activate.ps1"
    Write-Host "[+] Virtual environment activated." -ForegroundColor Green
}
catch {
    Write-Host "[!] Failed to activate virtual environment." -ForegroundColor Red
    Write-Host "[!] Try running: $projectRoot\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    Read-Host "[. Press Enter to exit"
    exit 1
}
Write-Host ""

# Upgrade pip and optionally install uv
Write-Host "[*] Upgrading pip..." -ForegroundColor Green
& $pythonExe -m pip install --upgrade pip

# Try to install uv (fast Python package manager)
Write-Host "[*] Installing uv (fast Python package manager)..." -ForegroundColor Green
try {
    & $pythonExe -m pip install uv
    Write-Host "[+] uv installed successfully." -ForegroundColor Green
    $uvInstalled = $true
}
catch {
    Write-Host "[!] Failed to install uv. Falling back to pip." -ForegroundColor Yellow
    $uvInstalled = $false
}

# Install dependencies from pyproject.toml or requirements.txt
if (Test-Path "$projectRoot\pyproject.toml") {
    Write-Host "[*] Installing dependencies from pyproject.toml..." -ForegroundColor Green
    if ($uvInstalled) {
        uv pip install -e .
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[!] uv failed to install dependencies. Falling back to pip." -ForegroundColor Yellow
            & $pythonExe -m pip install -e .
        }
    }
    else {
        & $pythonExe -m pip install -e .
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] Failed to install dependencies from pyproject.toml." -ForegroundColor Red
        Read-Host "[.] Press Enter to exit"
        exit 1
    }
}
elseif (Test-Path "$projectRoot\requirements.txt") {
    Write-Host "[*] Installing dependencies from requirements.txt..." -ForegroundColor Green
    if ($uvInstalled) {
        uv pip install -r "$projectRoot\requirements.txt"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[!] uv failed to install requirements. Falling back to pip." -ForegroundColor Yellow
            & $pythonExe -m pip install -r "$projectRoot\requirements.txt"
        }
    }
    else {
        & $pythonExe -m pip install -r "$projectRoot\requirements.txt"
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] Failed to install requirements." -ForegroundColor Red
        Read-Host "[.] Press Enter to exit"
        exit 1
    }
}
else {
    Write-Host "[!] No dependency file found (pyproject.toml or requirements.txt)." -ForegroundColor Red
    Read-Host "[.] Press Enter to exit"
    exit 1
}
Write-Host "[+] Dependencies installed successfully." -ForegroundColor Green
Write-Host ""

# Install Bitwarden CLI if requested
if ($Bitwarden) {
    Write-Host ""
    Write-Host "[*] ===========================================" -ForegroundColor Cyan
    Write-Host "[*] Bitwarden Secrets Manager CLI Setup" -ForegroundColor Cyan  
    Write-Host "[*] ===========================================" -ForegroundColor Cyan
    
    $bitwardenSuccess = Install-BitwardenCLI
    if ($bitwardenSuccess) {
        Write-Host "[+] Bitwarden CLI setup completed!" -ForegroundColor Green
        Write-Host "[*] You can now use 'bws' commands in this session" -ForegroundColor Cyan
        Write-Host "[*] For authentication, set environment variable:" -ForegroundColor Cyan
        Write-Host "[*]   `$env:BWS_ACCESS_TOKEN = 'your-token-here'" -ForegroundColor Gray
    }
    else {
        Write-Host "[!] Bitwarden CLI setup failed!" -ForegroundColor Red
        Write-Host "[!] You can manually download from: https://github.com/bitwarden/sdk-sm/releases" -ForegroundColor Yellow
    }
}

# Install Terraform CLI if requested
if ($Terraform) {
    Write-Host ""
    Write-Host "[*] ===========================================" -ForegroundColor Cyan
    Write-Host "[*] Terraform CLI Setup" -ForegroundColor Cyan  
    Write-Host "[*] ===========================================" -ForegroundColor Cyan
    
    $terraformSuccess = Install-TerraformCLI
    if ($terraformSuccess) {
        Write-Host "[+] Terraform CLI setup completed!" -ForegroundColor Green
        Write-Host "[*] You can now use 'terraform' commands in this session" -ForegroundColor Cyan
        Write-Host "[*] For authentication, run:" -ForegroundColor Cyan
        Write-Host "[*]   terraform login" -ForegroundColor Gray
        Write-Host "[*] Or set environment variable:" -ForegroundColor Cyan
        Write-Host "[*]   `$env:TERRAFORM_API_TOKEN = 'your-token-here'" -ForegroundColor Gray
    }
    else {
        Write-Host "[!] Terraform CLI setup failed!" -ForegroundColor Red
        Write-Host "[!] You can manually download from: https://www.terraform.io/downloads" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[+] ================================================" -ForegroundColor Cyan
Write-Host "[+] Setup completed successfully!" -ForegroundColor Green
Write-Host "[+] ================================================" -ForegroundColor Cyan
Write-Host "[+] Virtual environment is now active." -ForegroundColor Green

# Keep PowerShell open
Read-Host "[.] Press Enter to continue"
