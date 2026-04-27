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
.\scripts\Run.ps1 set --work-path $app output json

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
.\scripts\Run.ps1 sln
.\scripts\Run.ps1 solution init -h
.\scripts\Run.ps1 solution init --name "test-solution" --work-path $app

# ==============================================================================
# [REFERENCE] set — persist a workspace default into .platform/cli.yaml
# ==============================================================================

.\scripts\Run.ps1 set -h
.\scripts\Run.ps1 set
.\scripts\Run.ps1 set --work-path $app output json
.\scripts\Run.ps1 set --work-path $app output console
.\scripts\Run.ps1 set --work-path $app output text
.\scripts\Run.ps1 set --work-path $app verbose true
.\scripts\Run.ps1 set --work-path $app verbose false
.\scripts\Run.ps1 set --work-path $app quiet true
.\scripts\Run.ps1 set --work-path $app quiet false

# ==============================================================================
# [REFERENCE] unset — unset workspace defaults
# ==============================================================================

.\scripts\Run.ps1 unset -h
.\scripts\Run.ps1 unset
.\scripts\Run.ps1 unset --work-path $app output

# ==============================================================================
# [REFERENCE] config — list / unset workspace defaults
# ==============================================================================

.\scripts\Run.ps1 config -h

# list — show all current defaults
.\scripts\Run.ps1 config list --work-path $app
.\scripts\Run.ps1 config list --work-path $app --output json
.\scripts\Run.ps1 config list --work-path $app --output text

# unset — remove a specific default
.\scripts\Run.ps1 config --work-path $app unset output
.\scripts\Run.ps1 config --work-path $app unset verbose
.\scripts\Run.ps1 config --work-path $app unset quiet
.\scripts\Run.ps1 config --work-path $app unset work_path


# ==============================================================================
# [FLOW] set → list → unset lifecycle
# ==============================================================================

.\scripts\Run.ps1 set --work-path $app output json
.\scripts\Run.ps1 config --work-path $app --output json list
.\scripts\Run.ps1 config --work-path $app unset output
.\scripts\Run.ps1 config --work-path $app --output json list

