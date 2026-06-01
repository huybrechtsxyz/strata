<#
.SYNOPSIS
    Bumps VERSION.txt on a release branch, tags, and guides through the PR workflow.
.DESCRIPTION
    Creates a release/vX.Y.Z branch from main, updates VERSION.txt, commits, pushes
    the branch, and creates an annotated tag locally. Because main is protected, the
    tag must be pushed AFTER the PR is merged and ci-build passes on the PR commit —
    that is the commit ci-release will look up to download the dist artifact.
.PARAMETER Version
    The version number to release (e.g. 0.0.2). Must be in X.Y.Z format.
.EXAMPLE
    .\scripts\Release.ps1 -Version "0.0.2"

    Then follow the printed instructions:
      1. Open the PR URL printed by this script.
      2. Wait for ci-build to pass on the PR.
      3. Merge the PR (squash or merge commit — note the merge commit SHA).
      4. Push the tag from the merge commit: git push origin v0.0.2
.NOTES
    The tag push is intentionally separate — ci-release requires a successful
    ci-build run for the tagged commit before it can download the dist artifact.
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
$branch = "release/$tag"
$versionFile = Join-Path $projectRoot "VERSION.txt"

# Derive GitHub repo URL for the PR compare link
$remoteUrl = git remote get-url origin 2>$null
$repoPath = $remoteUrl -replace '\.git$', '' -replace '^https://github\.com/', ''

Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] Strata - Release $tag" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

# Read and compare versions
$currentRaw = (Get-Content $versionFile -Raw).Trim()
$currentVer = [System.Version]$currentRaw
$newVer = [System.Version]$Version

Write-Host "[*] VERSION.txt: $currentRaw" -ForegroundColor Yellow

if ($newVer -lt $currentVer) {
    Write-Host "[!] Version $Version is less than current $currentRaw. Aborting." -ForegroundColor Red
    exit 1
}

# Abort if tag already exists locally
$existingTag = git tag --list $tag
if ($existingTag) {
    Write-Host "[!] Tag $tag already exists locally. Aborting." -ForegroundColor Red
    exit 1
}

# Abort if working tree is dirty
$dirty = git status --porcelain
if ($dirty) {
    Write-Host "[!] Working tree has uncommitted changes. Commit or stash them first." -ForegroundColor Red
    git status --short
    exit 1
}

# Ensure we start from an up-to-date main
$currentBranch = git branch --show-current
if ($currentBranch -ne 'main') {
    Write-Host "[!] Must be on main branch to create a release. Currently on: $currentBranch" -ForegroundColor Red
    exit 1
}
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Could not fast-forward main from origin. Resolve divergence first." -ForegroundColor Red
    exit 1
}

# ── Equal: VERSION.txt already at target — just re-tag HEAD ─────────────────
if ($newVer -eq $currentVer) {
    Write-Host "[*] Version unchanged ($Version) — skipping commit, tagging HEAD." -ForegroundColor Yellow
    Write-Host ""

    git tag -a $tag -m "Release $tag"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] git tag failed." -ForegroundColor Red
        exit 1
    }

    $headCommit = git rev-parse --short HEAD
    Write-Host "[+] Tag $tag created on HEAD ($headCommit)" -ForegroundColor Green
    Write-Host ""
    Write-Host "[>] Push the tag to trigger ci-release:" -ForegroundColor Cyan
    Write-Host "     git push origin $tag" -ForegroundColor DarkCyan
    Write-Host ""
    exit 0
}

# ── Greater: bump VERSION.txt via a PR branch ────────────────────────────────
Write-Host "[*] VERSION.txt: $currentRaw → $Version" -ForegroundColor Yellow

# Abort if release branch already exists
$existingBranch = git branch --list $branch
if ($existingBranch) {
    Write-Host "[!] Branch $branch already exists. Aborting." -ForegroundColor Red
    exit 1
}

# 1. Create and switch to the release branch
git checkout -b $branch
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] Failed to create branch $branch." -ForegroundColor Red
    exit 1
}

# 2. Write new version
Set-Content -Path $versionFile -Value $Version -NoNewline

# 3. Pin all @main action refs in deploy-workspace.yml to the release tag
$workflowFile = Join-Path $projectRoot ".github" "workflows" "deploy-workspace.yml"
Write-Host "[*] Pinning action refs: @main → $tag" -ForegroundColor Yellow
(Get-Content $workflowFile -Raw) -replace '@main', "@$tag" | Set-Content $workflowFile -NoNewline

# 4. Stage and commit
git add VERSION.txt
git add .github/workflows/deploy-workspace.yml
git commit -m "chore: release $tag"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] git commit failed." -ForegroundColor Red
    exit 1
}

# 4. Push the release branch (triggers ci-build via PR)
git push -u origin $branch
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] git push failed." -ForegroundColor Red
    exit 1
}

# 5. Create annotated tag locally (do NOT push yet)
git tag -a $tag -m "Release $tag"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[!] git tag failed." -ForegroundColor Red
    exit 1
}

$branchCommit = git rev-parse --short HEAD

Write-Host ""
Write-Host "[+] Branch $branch pushed (commit $branchCommit)" -ForegroundColor Green
Write-Host "[+] Tag $tag created locally (not yet pushed)" -ForegroundColor Green
Write-Host ""
Write-Host "[>] Next steps:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. Open the PR and wait for ci-build to pass:" -ForegroundColor White
Write-Host "     https://github.com/$repoPath/compare/main...$branch" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  2. Merge the PR into main." -ForegroundColor White
Write-Host ""
Write-Host "  3. After merging, move the tag to the merge commit and push:" -ForegroundColor White
Write-Host "     git checkout main" -ForegroundColor DarkCyan
Write-Host "     git pull --ff-only origin main" -ForegroundColor DarkCyan
Write-Host "     git tag -f $tag" -ForegroundColor DarkCyan
Write-Host "     git push origin $tag" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "     The tag push triggers ci-release, which looks up the ci-build" -ForegroundColor Gray
Write-Host "     run for that commit to download the dist artifact." -ForegroundColor Gray
Write-Host ""
