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

cd $app
..\scripts\Run.ps1 solution init --name "test-solution"

.\scripts\Run.ps1 config set --work-path $app output json

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

.\scripts\Run.ps1 solution -h
.\scripts\Run.ps1 solution
.\scripts\Run.ps1 solution init -h
.\scripts\Run.ps1 solution init --name "test-solution" --work-path $app

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
.\scripts\Run.ps1 config info --work-path $app
.\scripts\Run.ps1 config info --work-path $app --output json
.\scripts\Run.ps1 config info --work-path $app --output text

# unset - remove a specific default (revert to built-in or env var value)
.\scripts\Run.ps1 config unset -h
.\scripts\Run.ps1 config unset
.\scripts\Run.ps1 config unset --work-path $app output json
.\scripts\Run.ps1 config unset --work-path $app output console
.\scripts\Run.ps1 config unset --work-path $app output text
.\scripts\Run.ps1 config unset --work-path $app verbose true
.\scripts\Run.ps1 config unset --work-path $app verbose false
.\scripts\Run.ps1 config unset --work-path $app quiet true
.\scripts\Run.ps1 config unset --work-path $app quiet false
