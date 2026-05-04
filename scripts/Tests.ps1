<#
  Tests.ps1  Manual test reference for xyz-platform CLI
  
  Two sections:
    [FLOW]      End-to-end workflow test  run top to bottom to validate the full session lifecycle
    [REFERENCE] Per-command variations  pick individual lines to test specific flags
#>


# ------------------------------------------------------------------------------
# [FLOW] End-to-end session lifecycle
# [FLOW] Optional: set env vars to test the resolution order
#   Explicit flag > XYZ_* env var > .platform/cli.yaml > built-in default
# ------------------------------------------------------------------------------
# $env:XYZ_WORK_PATH  = (Resolve-Path $app).Path   # auto-resolve work path
# $env:XYZ_OUTPUT     = "json"                      # default output format
# $env:XYZ_VERBOSE    = "true"                      # enable verbose log replay
# $env:XYZ_QUIET      = "true"                      # suppress all output

$app = ".app"

New-Item -Path $app -ItemType Directory -Force

.\scripts\Run.ps1 solution init --name "test-solution" --work-path $app

.\scripts\Run.ps1 config set --work-path $app output console

.\scripts\Run.ps1 solution repo add traefik https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app

.\scripts\Run.ps1 solution repo sync --name traefik --force --work-path $app

Remove-Item -Path $app -Recurse -Force -ErrorAction SilentlyContinue


# ==============================================================================
# [REFERENCE] Basic commands
# ==============================================================================

.\scripts\Run.ps1 -h
.\scripts\Run.ps1

.\scripts\Run.ps1 version -h
.\scripts\Run.ps1 version
.\scripts\Run.ps1 version --output console
.\scripts\Run.ps1 version --output json
.\scripts\Run.ps1 version --output text

# ==============================================================================
# [REFERENCE] config — persist workspace defaults into .platform/cli.yaml
# ==============================================================================

.\scripts\Run.ps1 config -h
.\scripts\Run.ps1 config

# set — define a default value for the current workspace
.\scripts\Run.ps1 config set -h
.\scripts\Run.ps1 config set
.\scripts\Run.ps1 config set --work-path $app output json
.\scripts\Run.ps1 config set --work-path $app output console
.\scripts\Run.ps1 config set --work-path $app output text
.\scripts\Run.ps1 config set --work-path $app verbose true
.\scripts\Run.ps1 config set --work-path $app verbose false
.\scripts\Run.ps1 config set --work-path $app quiet true
.\scripts\Run.ps1 config set --work-path $app quiet false

# info — show all current defaults
.\scripts\Run.ps1 config list -h
.\scripts\Run.ps1 config list
.\scripts\Run.ps1 config list --work-path $app
.\scripts\Run.ps1 config list --work-path $app --output json
.\scripts\Run.ps1 config list --work-path $app --output text

# unset - remove a specific default (revert to built-in or env var value)
.\scripts\Run.ps1 config unset -h
.\scripts\Run.ps1 config unset
.\scripts\Run.ps1 config unset --work-path $app output
.\scripts\Run.ps1 config unset --work-path $app verbose
.\scripts\Run.ps1 config unset --work-path $app quiet 

# ==============================================================================
# [REFERENCE] solution — Solution lifecycle management
# ==============================================================================

.\scripts\Run.ps1 solution -h
.\scripts\Run.ps1 solution
.\scripts\Run.ps1 solution init -h
.\scripts\Run.ps1 solution init --name "test-solution" --work-path $app

# solution clean - remove workspace artifacts without touching state

.\scripts\Run.ps1 solution clean -h
.\scripts\Run.ps1 solution clean
.\scripts\Run.ps1 solution clean --work-path $app --dry-run
.\scripts\Run.ps1 solution clean --work-path $app
.\scripts\Run.ps1 solution clean --work-path $app --output json

# solution repo — manage repositories registered in the solution

.\scripts\Run.ps1 solution repo -h
.\scripts\Run.ps1 solution repo

# solution repo add — register a repository (no cloning; clone deferred to xyz solution sync)
.\scripts\Run.ps1 solution repo add -h
.\scripts\Run.ps1 solution repo add
.\scripts\Run.ps1 solution repo add my-repo https://github.com/huybrechtsxyz/xyz-traefik.git --work-path $app
.\scripts\Run.ps1 solution repo add my-repo-branch https://github.com/huybrechtsxyz/xyz-traefik.git --branch main --work-path $app
.\scripts\Run.ps1 solution repo add my-repo-path https://github.com/huybrechtsxyz/xyz-traefik.git --path repos/custom --work-path $app
.\scripts\Run.ps1 solution repo add my-repo-all https://github.com/huybrechtsxyz/xyz-traefik.git --branch develop --path repos/custom --work-path $app

# solution repo add — structured output
.\scripts\Run.ps1 solution repo add json-repo https://github.com/org/json-repo.git --work-path $app --output json
.\scripts\Run.ps1 solution repo add text-repo https://github.com/org/text-repo.git --work-path $app --output text

# solution repo add — duplicate name should fail
.\scripts\Run.ps1 solution repo add my-repo https://github.com/org/my-repo.git --work-path $app

# solution repo list — show repositories registered in the solution
.\scripts\Run.ps1 solution repo list -h
.\scripts\Run.ps1 solution repo list
.\scripts\Run.ps1 solution repo list --work-path $app --output console
.\scripts\Run.ps1 solution repo list --work-path $app --output json
.\scripts\Run.ps1 solution repo list --work-path $app --output text

# list a single repo by name
.\scripts\Run.ps1 solution repo list --name my-repo --work-path $app
.\scripts\Run.ps1 solution repo list --name my-repo --work-path $app --output json

# list a name that doesn't exist — should return an error
.\scripts\Run.ps1 solution repo list --name no-such-repo --work-path $app

# solution repo remove — unregister a repository from the solution
.\scripts\Run.ps1 solution repo remove -h
.\scripts\Run.ps1 solution repo remove

# remove from registry only (leaves the local folder on disk)
.\scripts\Run.ps1 solution repo remove my-repo --work-path $app
.\scripts\Run.ps1 solution repo remove my-repo --work-path $app --output json
.\scripts\Run.ps1 solution repo remove my-repo --work-path $app --output text

# remove and delete the local clone from disk
.\scripts\Run.ps1 solution repo remove my-repo --work-path $app --purge
.\scripts\Run.ps1 solution repo remove my-repo --work-path $app --purge --output json

# remove a name that doesn't exist — should return an error
.\scripts\Run.ps1 solution repo remove no-such-repo --work-path $app

# solution repo sync — clone or update registered repositories
.\scripts\Run.ps1 solution repo sync -h
.\scripts\Run.ps1 solution repo sync
.\scripts\Run.ps1 solution repo sync --work-path $app

# sync all repos
.\scripts\Run.ps1 solution repo sync --work-path $app
.\scripts\Run.ps1 solution repo sync --work-path $app --output json
.\scripts\Run.ps1 solution repo sync --work-path $app --output text

# sync a single repo by name
.\scripts\Run.ps1 solution repo sync --name my-repo --work-path $app
.\scripts\Run.ps1 solution repo sync --name my-repo --work-path $app --output json

# force — pull even when the working tree has uncommitted changes
.\scripts\Run.ps1 solution repo sync --work-path $app --force
.\scripts\Run.ps1 solution repo sync --name my-repo --work-path $app --force

# sync a name that doesn't exist — should return an error
.\scripts\Run.ps1 solution repo sync --name no-such-repo --work-path $app

# ==============================================================================
# End of reference commands
# ==============================================================================

Remove-Item -Path $app -Recurse -Force -ErrorAction SilentlyContinue

