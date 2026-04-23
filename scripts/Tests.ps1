<#
  Tests.ps1  Manual test reference for xyz-platform CLI
  
  Two sections:
    [FLOW]      End-to-end workflow test  run top to bottom to validate the full session lifecycle
    [REFERENCE] Per-command variations  pick individual lines to test specific flags
#>


# ==============================================================================
# [FLOW] End-to-end session lifecycle
# ==============================================================================

$app = ".app"

New-Item -Path $app -ItemType Directory -Force


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

