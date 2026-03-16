<#
#>

# Main entry point for CLI commands
.\scripts\Run.ps1 -h

# Version command
.\scripts\Run.ps1 version
.\scripts\Run.ps1 version -h
.\scripts\Run.ps1 version --output text
.\scripts\Run.ps1 version --output json
.\scripts\Run.ps1 version --output raw

# Help command
.\scripts\Run.ps1 help
.\scripts\Run.ps1 help -h
.\scripts\Run.ps1 help terraform # Show help for terraform topic
.\scripts\Run.ps1 help tf         # Show topic not found

# Temporary app directory for testing
New-Item -Path .app -ItemType Directory -Force

# Clean up app directory after testing
Remove-Item -Path .app -Recurse -Force

# ============================================================
# Session command group
# ============================================================

.\scripts\Run.ps1 session -h

# session init
.\scripts\Run.ps1 session init -h
.\scripts\Run.ps1 session init --name platform --work-path .app
.\scripts\Run.ps1 session init --name platform --work-path .app --editor vscode
.\scripts\Run.ps1 session init --name platform --work-path .app --editor vscode --output json
.\scripts\Run.ps1 session init --name platform --work-path .app --editor vscode --output text
.\scripts\Run.ps1 session init --name platform --work-path .app --editor vscode --verbose
.\scripts\Run.ps1 session init --name platform --work-path .app --editor vscode --quiet
.\scripts\Run.ps1 session init --name platform --work-path .app --output json
.\scripts\Run.ps1 session init --name platform --work-path .app --output text
.\scripts\Run.ps1 session init --name platform --work-path .app --verbose
.\scripts\Run.ps1 session init --name platform --work-path .app --quiet

# session show
.\scripts\Run.ps1 session show -h
.\scripts\Run.ps1 session show --work-path .app
.\scripts\Run.ps1 session show --work-path .app --output json
.\scripts\Run.ps1 session show --work-path .app --output text
.\scripts\Run.ps1 session show --work-path .app --verbose

# session add — repository mode
.\scripts\Run.ps1 session add -h
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config"
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config" --output json
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config" --output text
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config" --verbose
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config" --quiet
.\scripts\Run.ps1 session add --name traefik --work-path .app --url "https://github.com/huybrechtsxyz/xyz-traefik.git" --branch main

# session add — config source mode (--config-file or --config-path, mutually exclusive with --url)
.\scripts\Run.ps1 session add --work-path .app --config-file "config/xyz/xyz-config.yaml"
.\scripts\Run.ps1 session add --name xyz_config --work-path .app --config-file "config/xyz/xyz-config.yaml"
.\scripts\Run.ps1 session add --work-path .app --config-path "config/xyz"
.\scripts\Run.ps1 session add --work-path .app --config-file "config/xyz/xyz-config.yaml" --output json
.\scripts\Run.ps1 session add --work-path .app --config-file "config/xyz/xyz-config.yaml" --output text
.\scripts\Run.ps1 session add --work-path .app --config-file "config/xyz/xyz-config.yaml" --verbose
.\scripts\Run.ps1 session add --work-path .app --config-file "config/xyz/xyz-config.yaml" --quiet

# session fetch
.\scripts\Run.ps1 session fetch -h
.\scripts\Run.ps1 session fetch --work-path .app
.\scripts\Run.ps1 session fetch --work-path .app --dry-run
.\scripts\Run.ps1 session fetch --work-path .app --force
.\scripts\Run.ps1 session fetch --work-path .app --name xyz_deploy
.\scripts\Run.ps1 session fetch --work-path .app --output json
.\scripts\Run.ps1 session fetch --work-path .app --output text
.\scripts\Run.ps1 session fetch --work-path .app --verbose
.\scripts\Run.ps1 session fetch --work-path .app --quiet

# session sync
.\scripts\Run.ps1 session sync -h
.\scripts\Run.ps1 session sync --work-path .app
.\scripts\Run.ps1 session sync --work-path .app --force
.\scripts\Run.ps1 session sync --work-path .app --output json
.\scripts\Run.ps1 session sync --work-path .app --output text
.\scripts\Run.ps1 session sync --work-path .app --verbose
.\scripts\Run.ps1 session sync --work-path .app --quiet

# session list
.\scripts\Run.ps1 session list -h
.\scripts\Run.ps1 session list --work-path .app
.\scripts\Run.ps1 session list --work-path .app --output json
.\scripts\Run.ps1 session list --work-path .app --output text
.\scripts\Run.ps1 session list --work-path .app --verbose

# session status
.\scripts\Run.ps1 session status -h
.\scripts\Run.ps1 session status --work-path .app
.\scripts\Run.ps1 session status --work-path .app --output json
.\scripts\Run.ps1 session status --work-path .app --output text
.\scripts\Run.ps1 session status --work-path .app --verbose

# session logs
.\scripts\Run.ps1 session logs -h
.\scripts\Run.ps1 session logs --work-path .app
.\scripts\Run.ps1 session logs --work-path .app --lines 100
.\scripts\Run.ps1 session logs --work-path .app --level ERROR
.\scripts\Run.ps1 session logs --work-path .app --last-exec
.\scripts\Run.ps1 session logs --work-path .app --output json
.\scripts\Run.ps1 session logs --work-path .app --output text
.\scripts\Run.ps1 session logs --work-path .app --verbose

# session remove
.\scripts\Run.ps1 session remove -h
.\scripts\Run.ps1 session remove --name config --work-path .app
.\scripts\Run.ps1 session remove --name config --work-path .app --dry-run
.\scripts\Run.ps1 session remove --name config --work-path .app --delete
.\scripts\Run.ps1 session remove --name config --work-path .app --delete --dry-run
.\scripts\Run.ps1 session remove --name config --work-path .app --output json
.\scripts\Run.ps1 session remove --name config --work-path .app --quiet

# session clean
.\scripts\Run.ps1 session clean -h
.\scripts\Run.ps1 session clean --work-path .app
.\scripts\Run.ps1 session clean --work-path .app --dry-run
.\scripts\Run.ps1 session clean --work-path .app --no-logs
.\scripts\Run.ps1 session clean --work-path .app --output json
.\scripts\Run.ps1 session clean --work-path .app --quiet

# session schemas
.\scripts\Run.ps1 session schemas -h
.\scripts\Run.ps1 session schemas --work-path .app
.\scripts\Run.ps1 session schemas --work-path .app --editor vscode
.\scripts\Run.ps1 session schemas --work-path .app --output-dir .xyz-platform/schemas
.\scripts\Run.ps1 session schemas --work-path .app --editor vscode --output-dir .xyz-platform/schemas
.\scripts\Run.ps1 session schemas --work-path .app --output json
.\scripts\Run.ps1 session schemas --work-path .app --output text
.\scripts\Run.ps1 session schemas --work-path .app --verbose
.\scripts\Run.ps1 session schemas --work-path .app --quiet
.\scripts\Run.ps1 session schemas --work-path .app --editor vscode --quiet

# ============================================================
# Tools command group
# ============================================================

.\scripts\Run.ps1 tools -h

# tools status
.\scripts\Run.ps1 tools status -h
.\scripts\Run.ps1 tools status
.\scripts\Run.ps1 tools status --work-path .app
.\scripts\Run.ps1 tools status --work-path .app --output json
.\scripts\Run.ps1 tools status --work-path .app --output text
.\scripts\Run.ps1 tools status --work-path .app --verbose
