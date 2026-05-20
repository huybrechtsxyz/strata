#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Manual smoke-test suite for strata CLI commands.

.DESCRIPTION
    Runs a comprehensive set of manual tests for all CLI commands.
    Validates command execution, exit codes, and output content.

.PARAMETER Category
    Test category to run: all, help, version, status, clean, config, audit,
    repo, profile, ref, validate, build, deploy, values

.PARAMETER ShowOutput
    Show captured command output for every test (useful for debugging failures).

.EXAMPLE
    .\Test-Commands.ps1
    .\Test-Commands.ps1 -Category validate
    .\Test-Commands.ps1 -Category config -ShowOutput

.NOTES
    Author: Vincent Huybrechts
    Version: 2.0.0
    Date: 2026-05-07
#>

param(
    [ValidateSet('all', 'help', 'version', 'status', 'clean', 'config', 'audit',
        'repo', 'profile', 'ref', 'validate', 'build', 'deploy', 'values', 'new', 'context')]
    [string]$Category = 'all',
    [switch]$ShowOutput
)


# =============================================================================
# Infrastructure
# =============================================================================

$script:TestResults = @{
    Passed = 0
    Failed = 0
    Tests  = @()
}

$ColorPass = 'Green'
$ColorFail = 'Red'
$ColorInfo = 'Cyan'

function Write-TestHeader {
    param([string]$Message)
    Write-Host "`n$('=' * 80)" -ForegroundColor $ColorInfo
    Write-Host "  $Message" -ForegroundColor $ColorInfo
    Write-Host "$('=' * 80)" -ForegroundColor $ColorInfo
}

function Write-TestResult {
    param([string]$TestName, [bool]$Passed, [string]$Detail = '')

    $script:TestResults.Tests += @{ Name = $TestName; Passed = $Passed; Detail = $Detail }

    if ($Passed) {
        $script:TestResults.Passed++
        Write-Host "  [PASS] $TestName" -ForegroundColor $ColorPass
    }
    else {
        $script:TestResults.Failed++
        Write-Host "  [FAIL] $TestName" -ForegroundColor $ColorFail
        if ($Detail) { Write-Host "         $Detail" -ForegroundColor $ColorFail }
    }
}

<#
.SYNOPSIS
    Execute a CLI command and validate exit code + output patterns.

.PARAMETER Name          Human-readable test label.
.PARAMETER Args          Arguments forwarded to .\scripts\Run.ps1.
.PARAMETER ExitCode      Expected exit code (default: 0).
.PARAMETER Contains      Regex patterns that must appear in the merged output.
.PARAMETER NotContains   Regex patterns that must NOT appear in the merged output.
#>
function Test-Cmd {
    param(
        [string]   $Name,
        [string[]] $Args,
        [int]      $ExitCode = 0,
        [string[]] $Contains = @(),
        [string[]] $NotContains = @()
    )

    $joined = $Args -join ' '
    if ($ShowOutput) {
        Write-Host "  --> .\scripts\Run.ps1 $joined" -ForegroundColor Gray
    }

    $output = & pwsh -NonInteractive -NoProfile -File ".\scripts\Run.ps1" @Args 2>&1
    $actual = $LASTEXITCODE

    if ($actual -ne $ExitCode) {
        Write-TestResult $Name $false "Exit code $actual (expected $ExitCode)"
        if ($ShowOutput) { $output | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
        return
    }

    $flat = $output -join "`n"

    foreach ($pattern in $Contains) {
        if ($flat -notmatch $pattern) {
            Write-TestResult $Name $false "Missing pattern: $pattern"
            if ($ShowOutput) { $output | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
            return
        }
    }

    foreach ($pattern in $NotContains) {
        if ($flat -match $pattern) {
            Write-TestResult $Name $false "Unexpected pattern: $pattern"
            if ($ShowOutput) { $output | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
            return
        }
    }

    Write-TestResult $Name $true
    if ($ShowOutput) { $output | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
}

# =============================================================================
# Test: Help
# =============================================================================
function Test-HelpCommands {
    Write-TestHeader "help"

    Test-Cmd "help: main -h" `
        -Args @('-h') `
        -Contains @('XYZ Platform CLI', 'Usage:')

    Test-Cmd "help: help -h" `
        -Args @('help', '-h') `
        -Contains @('topic', 'list')

    Test-Cmd "help: list topics" `
        -Args @('help', '--list') `
        -Contains @('quickstart', 'workspace')

    Test-Cmd "help: built-in topic quickstart" `
        -Args @('help', 'quickstart') `
        -Contains @('quickstart')

    Test-Cmd "help: unknown topic exits 1" `
        -Args @('help', 'nonexistent-topic-xyz') `
        -ExitCode 1
}

# =============================================================================
# Test: Version
# =============================================================================
function Test-VersionCommands {
    Write-TestHeader "version"

    Test-Cmd "version: -h" `
        -Args @('version', '-h') `
        -Contains @('version')

    Test-Cmd "version: default output contains version number" `
        -Args @('version') `
        -Contains @('\d+\.\d+\.\d+')

    Test-Cmd "version: json output" `
        -Args @('version', '--output', 'json') `
        -Contains @('"version"') `
        -NotContains @('XYZ Platform CLI')

    Test-Cmd "version: text output is bare version number" `
        -Args @('version', '--output', 'text') `
        -Contains @('^\d+\.\d+\.\d+') `
        -NotContains @('XYZ Platform CLI')
}

# =============================================================================
# Test: Status
# =============================================================================
function Test-StatusCommands {
    Write-TestHeader "status"

    Test-Cmd "status: -h" `
        -Args @('status', '-h') `
        -Contains @('workspace', 'health')

    # status outside a workspace should exit 1 (no .strata/ folder)
    Test-Cmd "status: outside workspace exits 1" `
        -Args @('status', '--work-path', 'C:\Temp') `
        -ExitCode 1
}

# =============================================================================
# Test: Clean
# =============================================================================
function Test-CleanCommands {
    Write-TestHeader "clean"

    Test-Cmd "clean: -h" `
        -Args @('clean', '-h') `
        -Contains @('artifact', 'dry-run')

    Test-Cmd "clean: dry-run outside workspace exits 1" `
        -Args @('clean', '--dry-run', '--work-path', 'C:\Temp') `
        -ExitCode 1
}

# =============================================================================
# Test: Config
# =============================================================================
function Test-ConfigCommands {
    Write-TestHeader "config"

    Test-Cmd "config: -h" `
        -Args @('config', '-h') `
        -Contains @('set', 'unset', 'list', 'log')

    Test-Cmd "config set: -h" `
        -Args @('config', 'set', '-h') `
        -Contains @('KEY', 'VALUE')

    Test-Cmd "config unset: -h" `
        -Args @('config', 'unset', '-h') `
        -Contains @('KEY')

    Test-Cmd "config list: -h" `
        -Args @('config', 'list', '-h') `
        -Contains @('workspace', 'default')

    Test-Cmd "config list: outside workspace exits 1" `
        -Args @('config', 'list', '--work-path', 'C:\Temp') `
        -ExitCode 1

    Test-Cmd "config set: invalid key outside workspace exits 1" `
        -Args @('config', 'set', 'invalid_key', 'somevalue', '--work-path', 'C:\Temp') `
        -ExitCode 1

    Test-Cmd "config log: -h" `
        -Args @('config', 'log', '-h') `
        -Contains @('list', 'get', 'set', 'unset', 'reset')
}

# =============================================================================
# Test: Audit
# =============================================================================
function Test-AuditCommands {
    Write-TestHeader "audit"

    Test-Cmd "audit: -h" `
        -Args @('audit', '-h') `
        -Contains @('list')

    Test-Cmd "audit list: -h" `
        -Args @('audit', 'list', '-h') `
        -Contains @('lines', 'level', 'last')

    Test-Cmd "audit list: outside workspace exits 1" `
        -Args @('audit', 'list', '--work-path', 'C:\Temp') `
        -ExitCode 1
}

# =============================================================================
# Test: Repo
# =============================================================================
function Test-RepoCommands {
    Write-TestHeader "repo"

    Test-Cmd "repo: -h" `
        -Args @('repo', '-h') `
        -Contains @('add', 'remove', 'list', 'status', 'sync')

    Test-Cmd "repo add: -h" `
        -Args @('repo', 'add', '-h') `
        -Contains @('NAME', 'URL', 'branch', 'clone')

    Test-Cmd "repo list: -h" `
        -Args @('repo', 'list', '-h') `
        -Contains @('repo')

    Test-Cmd "repo status: -h" `
        -Args @('repo', 'status', '-h') `
        -Contains @('repo')

    Test-Cmd "repo sync: -h" `
        -Args @('repo', 'sync', '-h') `
        -Contains @('sync')

    Test-Cmd "repo remove: -h" `
        -Args @('repo', 'remove', '-h') `
        -Contains @('NAME')

    Test-Cmd "repo list: outside workspace exits 1" `
        -Args @('repo', 'list', '--work-path', 'C:\Temp') `
        -ExitCode 1
}

# =============================================================================
# Test: Profile
# =============================================================================
function Test-ProfileCommands {
    Write-TestHeader "profile"

    Test-Cmd "profile: -h" `
        -Args @('profile', '-h') `
        -Contains @('add', 'remove', 'list', 'activate')

    Test-Cmd "profile add: -h" `
        -Args @('profile', 'add', '-h') `
        -Contains @('NAME')

    Test-Cmd "profile remove: -h" `
        -Args @('profile', 'remove', '-h') `
        -Contains @('NAME')

    Test-Cmd "profile list: -h" `
        -Args @('profile', 'list', '-h') `
        -Contains @('profile')

    Test-Cmd "profile activate: -h" `
        -Args @('profile', 'activate', '-h') `
        -Contains @('NAME')

    Test-Cmd "profile list: outside workspace exits 1" `
        -Args @('profile', 'list', '--work-path', 'C:\Temp') `
        -ExitCode 1
}

# =============================================================================
# Test: Ref
# =============================================================================
function Test-RefCommands {
    Write-TestHeader "ref"

    Test-Cmd "ref: -h" `
        -Args @('ref', '-h') `
        -Contains @('env', 'config', 'data', 'secret')

    Test-Cmd "ref env: -h" `
        -Args @('ref', 'env', '-h') `
        -Contains @('add', 'remove', 'list')

    Test-Cmd "ref config: -h" `
        -Args @('ref', 'config', '-h') `
        -Contains @('add', 'remove', 'list')

    Test-Cmd "ref env add: -h" `
        -Args @('ref', 'env', 'add', '-h') `
        -Contains @('NAME', 'PATH')

    Test-Cmd "ref env list: outside workspace exits 1" `
        -Args @('ref', 'env', 'list', '--work-path', 'C:\Temp') `
        -ExitCode 1
}

# =============================================================================
# Test: Validate
# =============================================================================
function Test-ValidateCommands {
    Write-TestHeader "validate"

    Test-Cmd "validate: -h" `
        -Args @('validate', '-h') `
        -Contains @('FILE_PATH', 'deep')

    Test-Cmd "validate: valid environment file (exit 0)" `
        -Args @('validate', 'tests/data/environments/environment-standard.yaml') `
        -ExitCode 0

    Test-Cmd "validate: valid workspace file (exit 0)" `
        -Args @('validate', 'tests/data/workspaces/workspace-standard.yaml') `
        -ExitCode 0

    Test-Cmd "validate: valid deployment file (exit 0)" `
        -Args @('validate', 'tests/data/deployments/deployment-standard.yaml') `
        -ExitCode 0

    Test-Cmd "validate: invalid environment file (exit 3)" `
        -Args @('validate', 'tests/data/environments/environment-invalid.yaml') `
        -ExitCode 3

    Test-Cmd "validate: invalid deployment file (exit 3)" `
        -Args @('validate', 'tests/data/deployments/deployment-invalid.yaml') `
        -ExitCode 3

    Test-Cmd "validate: missing file (exit 1)" `
        -Args @('validate', 'tests/data/nonexistent-xyz.yaml') `
        -ExitCode 1

    Test-Cmd "validate: --deep flag accepted" `
        -Args @('validate', 'tests/data/environments/environment-standard.yaml', '--deep') `
        -ExitCode 1   # no initialized workspace → fails with exit 1, not 2

    Test-Cmd "validate: json output for valid file" `
        -Args @('validate', 'tests/data/environments/environment-standard.yaml', '--output', 'json') `
        -ExitCode 0 `
        -Contains @('"valid"')
}

# =============================================================================
# Test: Build
# =============================================================================
function Test-BuildCommands {
    Write-TestHeader "build"

    Test-Cmd "build: -h" `
        -Args @('build', '-h') `
        -Contains @('run', 'clean', 'plan')

    Test-Cmd "build run: -h" `
        -Args @('build', 'run', '-h') `
        -Contains @('file', 'dry-run')

    Test-Cmd "build clean: -h" `
        -Args @('build', 'clean', '-h') `
        -Contains @('file', 'dry-run')

    Test-Cmd "build plan: -h" `
        -Args @('build', 'plan', '-h') `
        -Contains @('file', 'stage', 'artifacts-only')

    Test-Cmd "build run: outside workspace exits 1" `
        -Args @('build', 'run', '--work-path', 'C:\Temp') `
        -ExitCode 1

    Test-Cmd "build run: dry-run with deployment file" `
        -Args @('build', 'run', '--file', 'tests/data/deployments/deployment-standard.yaml', '--dry-run') `
        -ExitCode 0
}

# =============================================================================
# Test: Deploy
# =============================================================================
function Test-DeployCommands {
    Write-TestHeader "deploy"

    Test-Cmd "deploy: -h" `
        -Args @('deploy', '-h') `
        -Contains @('run', 'status', 'history', 'destroy', 'health')

    Test-Cmd "deploy run: -h" `
        -Args @('deploy', 'run', '-h') `
        -Contains @('file', 'stage', 'force', 'dry-run')

    Test-Cmd "deploy status: -h" `
        -Args @('deploy', 'status', '-h') `
        -Contains @('file')

    Test-Cmd "deploy history: -h" `
        -Args @('deploy', 'history', '-h') `
        -Contains @('file')

    Test-Cmd "deploy destroy: -h" `
        -Args @('deploy', 'destroy', '-h') `
        -Contains @('file', 'force')

    Test-Cmd "deploy health: -h" `
        -Args @('deploy', 'health', '-h') `
        -Contains @('file')

    Test-Cmd "deploy run: outside workspace exits 1" `
        -Args @('deploy', 'run', '--work-path', 'C:\Temp') `
        -ExitCode 1
}

# =============================================================================
# Test: Values
# =============================================================================
function Test-ValuesCommands {
    Write-TestHeader "values"

    Test-Cmd "values: -h" `
        -Args @('values', '-h') `
        -Contains @('list', 'get')

    Test-Cmd "values list: -h" `
        -Args @('values', 'list', '-h') `
        -Contains @('file', 'stage', 'type', 'unresolved')

    Test-Cmd "values get: -h" `
        -Args @('values', 'get', '-h') `
        -Contains @('file')

    Test-Cmd "values list: missing required --file exits 2" `
        -Args @('values', 'list') `
        -ExitCode 2
}

# =============================================================================
# Test: New
# =============================================================================
function Test-NewCommands {
    Write-TestHeader "new"

    Test-Cmd "new: -h" `
        -Args @('new', '-h') `
        -Contains @('TEMPLATE', 'NAME', 'path')

    Test-Cmd "new: --list" `
        -Args @('new', '--list') `
        -ExitCode 0

    Test-Cmd "new: namespace in tmp dir (exit 0)" `
        -Args @('new', 'namespace', 'myapp', '--path', $env:TEMP) `
        -ExitCode 0

    Test-Cmd "new: unknown template exits 1" `
        -Args @('new', 'nonexistent_xyz_template', 'myapp') `
        -ExitCode 1

    Test-Cmd "new: existing file no --overwrite exits 1" `
        -Args @('new', 'namespace', 'myapp', '--path', $env:TEMP) `
        -ExitCode 1

    Test-Cmd "new: existing file with --overwrite exits 0" `
        -Args @('new', 'namespace', 'myapp', '--path', $env:TEMP, '--overwrite') `
        -ExitCode 0
}

# =============================================================================
# Test: Context
# =============================================================================
function Test-ContextCommands {
    Write-TestHeader "vars"

    Test-Cmd "vars: -h" `
        -Args @('vars', '-h') `
        -Contains @('set', 'unset', 'list')

    Test-Cmd "vars set: -h" `
        -Args @('vars', 'set', '-h') `
        -Contains @('KEY', 'VALUE')

    Test-Cmd "vars unset: -h" `
        -Args @('vars', 'unset', '-h') `
        -Contains @('KEY')

    Test-Cmd "vars list: -h" `
        -Args @('vars', 'list', '-h') `
        -Contains @('template')

    Test-Cmd "vars list: outside workspace exits 1" `
        -Args @('vars', 'list', '--work-path', 'C:\Temp') `
        -ExitCode 1

    Test-Cmd "vars set: outside workspace exits 1" `
        -Args @('vars', 'set', 'owner', 'myteam', '--work-path', 'C:\Temp') `
        -ExitCode 1
}

# =============================================================================
# Main Execution
# =============================================================================

Write-Host ""
Write-Host "XYZ PLATFORM - MANUAL SMOKE TEST SUITE" -ForegroundColor $ColorInfo
Write-Host "Category : $Category" -ForegroundColor Gray
Write-Host "Date     : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Gray
Write-Host ""

# Always run from the project root so .\scripts\Run.ps1 is reachable
$scriptDir = Split-Path -Parent $PSCommandPath
$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
Set-Location $projectRoot

# Dispatch
switch ($Category) {
    'all' {
        Test-HelpCommands
        Test-VersionCommands
        Test-StatusCommands
        Test-CleanCommands
        Test-ConfigCommands
        Test-AuditCommands
        Test-RepoCommands
        Test-ProfileCommands
        Test-RefCommands
        Test-ValidateCommands
        Test-NewCommands
        Test-ContextCommands
        Test-BuildCommands
        Test-DeployCommands
        Test-ValuesCommands
    }
    'help' { Test-HelpCommands }
    'version' { Test-VersionCommands }
    'status' { Test-StatusCommands }
    'clean' { Test-CleanCommands }
    'config' { Test-ConfigCommands }
    'audit' { Test-AuditCommands }
    'repo' { Test-RepoCommands }
    'profile' { Test-ProfileCommands }
    'ref' { Test-RefCommands }
    'validate' { Test-ValidateCommands }
    'new' { Test-NewCommands }
    'context' { Test-ContextCommands }
    'build' { Test-BuildCommands }
    'deploy' { Test-DeployCommands }
    'values' { Test-ValuesCommands }
}

# =============================================================================
# Summary
# =============================================================================

Write-TestHeader "Results"

$total = $script:TestResults.Tests.Count
Write-Host "  Total  : $total" -ForegroundColor White
Write-Host "  Passed : $($script:TestResults.Passed)" -ForegroundColor $ColorPass
Write-Host "  Failed : $($script:TestResults.Failed)" -ForegroundColor $ColorFail
Write-Host ""

if ($script:TestResults.Failed -gt 0) {
    Write-Host "  FAILED TESTS:" -ForegroundColor $ColorFail
    $script:TestResults.Tests |
    Where-Object { -not $_.Passed } |
    ForEach-Object { Write-Host "    - $($_.Name)  $($_.Detail)" -ForegroundColor $ColorFail }
    Write-Host ""
    Write-Host "RESULT: FAILED" -ForegroundColor $ColorFail
    exit 1
}
else {
    Write-Host "RESULT: ALL PASSED" -ForegroundColor $ColorPass
    exit 0
}