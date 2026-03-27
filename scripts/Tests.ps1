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

.\scripts\Run.ps1 -h

.\scripts\Run.ps1 version --output text

.\scripts\Run.ps1 tools status

.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode

.\scripts\Run.ps1 session source add --name xyz_git --work-path $app --url "https://github.com/huybrechtsxyz/xyz-platform.git"
.\scripts\Run.ps1 session source add --name xyz_local --work-path $app --url "../repo/xyz_configuration"
.\scripts\Run.ps1 session source add --name xyz_configuration --work-path $app --url "../repo/xyz_configuration"
.\scripts\Run.ps1 session source add --name xyz_infrastructure --work-path $app --url "../repo/xyz_infrastructure"

.\scripts\Run.ps1 session source list --work-path $app

.\scripts\Run.ps1 session source remove --name xyz_local --work-path $app
.\scripts\Run.ps1 session source remove --name xyz_git --work-path $app

# ==============================================================================
# [REFERENCE] Basic commands
# ==============================================================================

.\scripts\Run.ps1 -h

# version
.\scripts\Run.ps1 version -h
.\scripts\Run.ps1 version
.\scripts\Run.ps1 version --output text
.\scripts\Run.ps1 version --output json
.\scripts\Run.ps1 version --output raw

# help
.\scripts\Run.ps1 help -h
.\scripts\Run.ps1 help
.\scripts\Run.ps1 help terraform
.\scripts\Run.ps1 help tf         # topic not found

# ==============================================================================
# [REFERENCE] tools
# ==============================================================================

.\scripts\Run.ps1 tools -h

# tools status
.\scripts\Run.ps1 tools status -h
.\scripts\Run.ps1 tools status
.\scripts\Run.ps1 tools status --work-path $app
.\scripts\Run.ps1 tools status --env-file ./scripts/Tests.env
.\scripts\Run.ps1 tools status --work-path $app --output json
.\scripts\Run.ps1 tools status --work-path $app --output text
.\scripts\Run.ps1 tools status --work-path $app --verbose

# ==============================================================================
# [REFERENCE] session
# ==============================================================================

.\scripts\Run.ps1 session -h

# session init
.\scripts\Run.ps1 session init -h
.\scripts\Run.ps1 session init --name platform --work-path $app
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --output json
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --output text
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --verbose
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --quiet

# session clean

.\scripts\Run.ps1 session clean -h
.\scripts\Run.ps1 session clean --work-path $app
.\scripts\Run.ps1 session clean --work-path $app --output json
.\scripts\Run.ps1 session clean --work-path $app --output text
.\scripts\Run.ps1 session clean --work-path $app --verbose
.\scripts\Run.ps1 session clean --work-path $app --quiet

# session add  url mode
.\scripts\Run.ps1 session source add -h
.\scripts\Run.ps1 session source add --name xyz_configuration --work-path $app --url "../repo/xyz_configuration"

# session add  config-file / config-path mode
.\scripts\Run.ps1 session source add --work-path $app --config-file "repo/xyz_configuration/config/xyz-config.yaml"
.\scripts\Run.ps1 session source add --name xyz_configuration --work-path $app --config-file "repo/xyz_configuration/config/xyz-config.yaml"
.\scripts\Run.ps1 session source add --name xyz_git --work-path $app --url "https://github.com/huybrechtsxyz/xyz-platform.git"
