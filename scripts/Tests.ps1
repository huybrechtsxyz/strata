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

# Version info
.\scripts\Run.ps1 version -h
.\scripts\Run.ps1 version
.\scripts\Run.ps1 version --output text
.\scripts\Run.ps1 version --output json
.\scripts\Run.ps1 version --output raw

# Help topics
.\scripts\Run.ps1 help -h
.\scripts\Run.ps1 help
.\scripts\Run.ps1 help terraform
.\scripts\Run.ps1 help tf         # topic not found

# ==============================================================================
# [REFERENCE] tools
# ==============================================================================

# Tools help
.\scripts\Run.ps1 tools -h

# Tools status
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

# Session help
.\scripts\Run.ps1 session -h

# Session init
.\scripts\Run.ps1 session init -h
.\scripts\Run.ps1 session init --name platform --work-path $app
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --output json
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --output text
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --verbose
.\scripts\Run.ps1 session init --name platform --work-path $app --editor vscode --quiet

# Session clean
.\scripts\Run.ps1 session clean -h
.\scripts\Run.ps1 session clean --work-path $app
.\scripts\Run.ps1 session clean --work-path $app --output json
.\scripts\Run.ps1 session clean --work-path $app --output text
.\scripts\Run.ps1 session clean --work-path $app --verbose
.\scripts\Run.ps1 session clean --work-path $app --quiet

# Session logs
.\scripts\Run.ps1 session logs -h
.\scripts\Run.ps1 session logs --work-path $app
.\scripts\Run.ps1 session logs --work-path $app --lines 100
.\scripts\Run.ps1 session logs --work-path $app --level ERROR
.\scripts\Run.ps1 session logs --work-path $app --last-exec
.\scripts\Run.ps1 session logs --work-path $app --output json
.\scripts\Run.ps1 session logs --work-path $app --output text
.\scripts\Run.ps1 session logs --work-path $app --verbose

# Session source
.\scripts\Run.ps1 session source -h

# Session Source Add - url mode
.\scripts\Run.ps1 session source add -h
.\scripts\Run.ps1 session source add --name xyz_local1 --work-path $app --url "../repo/xyz_configuration"
.\scripts\Run.ps1 session source add --name xyz_local2 --work-path $app --url "../repo/xyz_configuration" --output json
.\scripts\Run.ps1 session source add --name xyz_local3 --work-path $app --url "../repo/xyz_configuration" --output text
.\scripts\Run.ps1 session source add --name xyz_git --work-path $app --url "https://github.com/huybrechtsxyz/xyz-platform.git" --verbose

# Session Source List
.\scripts\Run.ps1 session source list -h
.\scripts\Run.ps1 session source list --work-path $app
.\scripts\Run.ps1 session source list --work-path $app --output json
.\scripts\Run.ps1 session source list --work-path $app --output text
.\scripts\Run.ps1 session source list --work-path $app --verbose

# Session Source Remove
.\scripts\Run.ps1 session source remove -h
.\scripts\Run.ps1 session source remove --name xyz_local1 --work-path $app
.\scripts\Run.ps1 session source remove --name xyz_local2 --work-path $app --output json
.\scripts\Run.ps1 session source remove --name xyz_local3 --work-path $app --output text
.\scripts\Run.ps1 session source remove --name xyz_git --work-path $app --verbose

# Session Dotenv
.\scripts\Run.ps1 session dotenv -h

# Session Dotenv Add
.\scripts\Run.ps1 session dotenv add --name test1 --work-path $app --env-file "../scripts/Tests.env"
.\scripts\Run.ps1 session dotenv add --name test2 --work-path $app --env-file "../scripts/Tests.env" --output json
.\scripts\Run.ps1 session dotenv add --name test3 --work-path $app --env-file "../scripts/Tests.env" --output text
.\scripts\Run.ps1 session dotenv add --name test4 --work-path $app --env-file "../scripts/Tests.env" --verbose

# Session Dotenv List
.\scripts\Run.ps1 session dotenv list -h
.\scripts\Run.ps1 session dotenv list --work-path $app
.\scripts\Run.ps1 session dotenv list --work-path $app --output json
.\scripts\Run.ps1 session dotenv list --work-path $app --output text
.\scripts\Run.ps1 session dotenv list --work-path $app --verbose

# Session Dotenv Remove
.\scripts\Run.ps1 session dotenv remove -h
.\scripts\Run.ps1 session dotenv remove --name test1 --work-path $app
.\scripts\Run.ps1 session dotenv remove --name test2 --work-path $app --output json
.\scripts\Run.ps1 session dotenv remove --name test3 --work-path $app --output text
.\scripts\Run.ps1 session dotenv remove --name test4 --work-path $app --verbose













