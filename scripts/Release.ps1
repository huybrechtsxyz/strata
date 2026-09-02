<#
.SYNOPSIS
    Two-phase release helper: draft a release branch/PR, then publish the tags once merged.
.DESCRIPTION
    Phase 1 (-Draft, also the default): creates a release/vX.Y.Z branch from main, updates
    VERSION.txt, commits, pushes the branch, and creates an annotated tag locally. Because
    main is protected, the exact tag is NOT pushed yet — only after the PR is merged.

    Phase 2 (-Publish): run AFTER the release PR has been merged into main. Verifies
    VERSION.txt on main's HEAD matches -Version (i.e. the PR really was merged), verifies
    the exact tag doesn't already exist on origin (no re-publish/duplicate release), verifies
    ci-build succeeded for that exact commit (via gh CLI — the same lookup ci-release.yml
    performs), then moves both tags to HEAD and pushes them. Pushing the exact tag triggers
    ci-release.yml, which creates the GitHub Release automatically (dist/sbom/vsix assets +
    generated notes) — no manual "Draft a new release" step in the GitHub UI is needed. On
    success, also deletes the now-merged release/vX.Y.Z branch, both locally (if present)
    and on origin (non-fatal if either is already gone, e.g. GitHub's delete-branch-on-merge).

    Two tags are maintained per release:
      - Exact tag   (e.g. v0.0.5) — triggers ci-release; use for auditable pinning.
      - Major tag   (e.g. v0)     — moving tag, always points to the latest release in
                                    the major line. GitHub Action consumers pin to this
                                    tag to receive non-breaking updates automatically
                                    without changing their workflow refs.

    The @v0 refs inside strata-deploy-workspace.yml and all composite actions resolve via the
    major tag — no file edits are needed on each release.
.PARAMETER Version
    The version number to release (e.g. 0.0.5). Must be in X.Y.Z format.
.PARAMETER Draft
    Phase 1 — create the release branch/PR. This is the default when neither -Draft nor
    -Publish is specified (preserves existing single-phase usage).
.PARAMETER Publish
    Phase 2 — run after the release PR is merged into main. Verifies, tags, and pushes,
    triggering ci-release to create the GitHub Release. Mutually exclusive with -Draft.
.EXAMPLE
    .\scripts\Release.ps1 -Version "0.0.5"
    .\scripts\Release.ps1 -Version "0.0.5" -Draft

    Both forms are equivalent (phase 1). Then:
      1. Open the PR URL printed by this script.
      2. Wait for ci-build to pass on the PR.
      3. Merge the PR into main.
      4. Run: .\scripts\Release.ps1 -Version "0.0.5" -Publish
.EXAMPLE
    .\scripts\Release.ps1 -Version "0.0.5" -Publish

    Phase 2, run after the PR from phase 1 has been merged into main. Moves both tags to
    main's HEAD and pushes them, which triggers ci-release to build the GitHub Release.
.NOTES
    The tag pushes are intentionally separate from phase 1 — ci-release requires a
    successful ci-build run for the tagged commit before it can download the dist artifact.
#>

param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [switch]$Draft,

    [switch]$Publish
)

if ($Draft -and $Publish) {
    Write-Host "[!] -Draft and -Publish are mutually exclusive." -ForegroundColor Red
    exit 1
}

# Default to -Draft when neither switch is given (preserves existing single-phase usage)
if (-not $Draft -and -not $Publish) {
    $Draft = $true
}

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

$phaseLabel = if ($Publish) { "Publish" } else { "Draft" }
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host "[*] Strata - $phaseLabel $tag" -ForegroundColor Cyan
Write-Host "[*] ==========================================" -ForegroundColor Cyan
Write-Host ""

$majorTag = "v$(([System.Version]$Version).Major)"

# ============================================================================
# Phase 2: -Publish — run after the release PR (from phase 1) is merged into
# main. Verifies, moves both tags to HEAD, and pushes them, triggering
# ci-release to build the GitHub Release automatically.
# ============================================================================
if ($Publish) {
    # Abort if working tree is dirty
    $dirty = git status --porcelain
    if ($dirty) {
        Write-Host "[!] Working tree has uncommitted changes. Commit or stash them first." -ForegroundColor Red
        git status --short
        exit 1
    }

    # Must be on main
    $currentBranch = git branch --show-current
    if ($currentBranch -ne 'main') {
        Write-Host "[!] Must be on main branch to publish a release. Currently on: $currentBranch" -ForegroundColor Red
        exit 1
    }

    git pull --ff-only origin main
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] Could not fast-forward main from origin. Resolve divergence first." -ForegroundColor Red
        exit 1
    }

    # VERSION.txt on HEAD must already equal the target version — proves the
    # release PR from phase 1 was actually merged, not some other commit.
    $currentRaw = (Get-Content $versionFile -Raw).Trim()
    if ($currentRaw -ne $Version) {
        Write-Host "[!] VERSION.txt on main is $currentRaw, expected $Version." -ForegroundColor Red
        Write-Host "[!] Has the release/$tag PR been merged into main yet?" -ForegroundColor Red
        exit 1
    }

    # Abort if the exact tag already exists on origin — avoids a duplicate/
    # re-published release.
    $remoteTag = git ls-remote --tags origin "refs/tags/$tag"
    if ($remoteTag) {
        Write-Host "[!] Tag $tag already exists on origin. Aborting to avoid re-publishing." -ForegroundColor Red
        exit 1
    }

    # Verify ci-build succeeded for HEAD — the same lookup ci-release.yml
    # performs; fail fast here instead of pushing a tag ci-release can't use.
    $ghAvailable = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $ghAvailable) {
        Write-Host "[!] GitHub CLI (gh) not found - cannot verify ci-build status. Install gh or verify manually." -ForegroundColor Red
        exit 1
    }
    $headSha = git rev-parse HEAD
    $runId = gh run list --workflow=ci-build.yml --commit=$headSha --status=success --limit=1 --json databaseId --jq ".[0].databaseId" 2>$null
    if ([string]::IsNullOrWhiteSpace($runId) -or $runId -eq 'null') {
        Write-Host "[!] No successful ci-build run found for commit $headSha. Aborting." -ForegroundColor Red
        Write-Host "[!] ci-release would fail the same lookup — wait for ci-build to pass on main first." -ForegroundColor Red
        exit 1
    }
    Write-Host "[+] ci-build succeeded for $headSha (run $runId)" -ForegroundColor Green

    # Move/recreate the exact tag as annotated (consistent with phase 1),
    # and force-move the lightweight major tag, both to HEAD.
    git tag -d $tag 2>$null | Out-Null
    git tag -a $tag -m "Release $tag"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] git tag failed." -ForegroundColor Red
        exit 1
    }
    git tag -f $majorTag
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] Failed to move major tag $majorTag." -ForegroundColor Red
        exit 1
    }

    git push origin $tag
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] git push of $tag failed." -ForegroundColor Red
        exit 1
    }
    git push origin $majorTag --force
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] git push of $majorTag failed." -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    Write-Host "[+] Pushed $tag and $majorTag ($headSha)" -ForegroundColor Green
    Write-Host "[+] ci-release will now build the GitHub Release automatically (no manual UI step needed)." -ForegroundColor Green
    Write-Host ""

    # Clean up the release branch — it's merged into main and no longer needed.
    # Non-fatal: GitHub's delete-branch-on-merge often already removed the
    # remote branch, and this working copy may never have had it locally.
    $localBranchExists = git branch --list $branch
    if ($localBranchExists) {
        git branch -d $branch 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[+] Deleted local branch $branch" -ForegroundColor Green
        }
        else {
            Write-Host "[!] Could not delete local branch $branch (skipped)" -ForegroundColor Yellow
        }
    }

    $remoteBranchExists = git ls-remote --heads origin $branch
    if ($remoteBranchExists) {
        git push origin --delete $branch 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[+] Deleted remote branch origin/$branch" -ForegroundColor Green
        }
        else {
            Write-Host "[!] Could not delete remote branch origin/$branch (skipped)" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "[*] Remote branch origin/$branch already gone (likely auto-deleted on merge)" -ForegroundColor Gray
    }

    Write-Host ""
    Write-Host "[>] Watch it:" -ForegroundColor Cyan
    Write-Host "     gh run list --workflow=ci-release.yml --commit=$headSha" -ForegroundColor DarkCyan
    Write-Host ""
    exit 0
}

# ============================================================================
# Phase 1: -Draft (default) — create the release branch/PR.
# ============================================================================

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

# -- Equal: VERSION.txt already at target -- just re-tag HEAD ---------------
if ($newVer -eq $currentVer) {
    Write-Host "[*] Version unchanged ($Version) - skipping commit, tagging HEAD." -ForegroundColor Yellow
    Write-Host ""

    git tag -a $tag -m "Release $tag"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] git tag failed." -ForegroundColor Red
        exit 1
    }

    git tag -f $majorTag
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] Failed to move major tag $majorTag." -ForegroundColor Red
        exit 1
    }

    $headCommit = git rev-parse --short HEAD
    Write-Host "[+] Tag $tag created on HEAD ($headCommit)" -ForegroundColor Green
    Write-Host "[+] Major tag $majorTag moved to HEAD ($headCommit)" -ForegroundColor Green
    Write-Host ""
    Write-Host "[>] Push both tags to trigger ci-release and update Action consumers:" -ForegroundColor Cyan
    Write-Host "     git push origin $tag" -ForegroundColor DarkCyan
    Write-Host "     git push origin $majorTag --force" -ForegroundColor DarkCyan
    Write-Host ""
    exit 0
}

# -- Greater: bump VERSION.txt via a PR branch ------------------------------
Write-Host "[*] VERSION.txt: $currentRaw -> $Version" -ForegroundColor Yellow

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

# 2b. Sync VS Code extension version
$extensionPackageJson = Join-Path $projectRoot "src\vscode\package.json"
$pkgContent = Get-Content $extensionPackageJson -Raw
$pkgContent = $pkgContent -replace '("version"\s*:\s*")[^"]*(")', "`${1}$Version`${2}"
Set-Content -Path $extensionPackageJson -Value $pkgContent -NoNewline

# 3. Pin all @main action refs in strata-deploy-workspace.yml to the release tag
# 4. Stage and commit
git add VERSION.txt src/vscode/package.json
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
Write-Host "  3. After merging, publish the release (tags + triggers ci-release):" -ForegroundColor White
Write-Host "     .\scripts\Release.ps1 -Version $Version -Publish" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "     This verifies VERSION.txt on main and that ci-build succeeded for the" -ForegroundColor Gray
Write-Host "     merge commit, then moves both tags to HEAD and pushes them. The exact-tag" -ForegroundColor Gray
Write-Host "     push triggers ci-release, which downloads the dist artifact and creates" -ForegroundColor Gray
Write-Host "     the GitHub Release automatically — no manual UI step needed. The major" -ForegroundColor Gray
Write-Host "     tag ($majorTag) lets GitHub Action consumers pin to the major line and" -ForegroundColor Gray
Write-Host "     receive non-breaking updates automatically." -ForegroundColor Gray
Write-Host ""
