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
.\scripts\Run.ps1 help terraform # Show help for version command
.\scripts\Run.ps1 help tf # Show topic not found

# Temporary app directory for testing
# Create .platform directory for testing
New-Item -Path .app -ItemType Directory -Force

# Clean up .platform directory after testing
Remove-Item -Path .app -Recurse -Force

# Session command
.\scripts\Run.ps1 session -h
.\scripts\Run.ps1 session init -h
.\scripts\Run.ps1 session init --name platform --work-path .app
.\scripts\Run.ps1 session init --name platform --work-path .app --output json
.\scripts\Run.ps1 session init --name platform --work-path .app --output text
.\scripts\Run.ps1 session init --name platform --work-path .app --verbose
.\scripts\Run.ps1 session init --name platform --work-path .app --quiet

# Show session command
.\scripts\Run.ps1 session show -h
.\scripts\Run.ps1 session show --work-path .app
.\scripts\Run.ps1 session show --work-path .app --output json
.\scripts\Run.ps1 session show --work-path .app --output text
.\scripts\Run.ps1 session show --work-path .app --verbose

# Add repo command
.\scripts\Run.ps1 session add -h
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config"
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config" --output json
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config" --output text
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config" --verbose
.\scripts\Run.ps1 session add --name config --work-path .app --url "../config" --quiet

# Log session command
.\scripts\Run.ps1 session logs -h
.\scripts\Run.ps1 session logs --work-path .app
.\scripts\Run.ps1 session logs --work-path .app --output json
.\scripts\Run.ps1 session logs --work-path .app --output text
.\scripts\Run.ps1 session logs --work-path .app --verbose

.\scripts\Run.ps1 session list -h
.\scripts\Run.ps1 session list --work-path .app
.\scripts\Run.ps1 session list --work-path .app --output json
.\scripts\Run.ps1 session list --work-path .app --output text

.\scripts\Run.ps1 session status -h
.\scripts\Run.ps1 session status --work-path .app
.\scripts\Run.ps1 session status --work-path .app --output json
.\scripts\Run.ps1 session status --work-path .app --output text

.\scripts\Run.ps1 session remove -h
.\scripts\Run.ps1 session remove --name config --work-path .app
.\scripts\Run.ps1 session remove --name config --work-path .app --dry-run
.\scripts\Run.ps1 session remove --name config --work-path .app --delete
.\scripts\Run.ps1 session remove --name config --work-path .app --delete --dry-run
.\scripts\Run.ps1 session remove --name config --work-path .app --output json

.\scripts\Run.ps1 session clean -h
.\scripts\Run.ps1 session clean --work-path .app
.\scripts\Run.ps1 session clean --work-path .app --dry-run
.\scripts\Run.ps1 session clean --work-path .app --no-logs
.\scripts\Run.ps1 session clean --work-path .app --output json

# Add with branch
.\scripts\Run.ps1 session add --name traefik --work-path .app --url "https://github.com/huybrechtsxyz/xyz-traefik.git" --branch develop

# Tools command
.\scripts\Run.ps1 tools -h
.\scripts\Run.ps1 tools status
.\scripts\Run.ps1 tools status --work-path .app
.\scripts\Run.ps1 tools status --work-path .app --output json
.\scripts\Run.ps1 tools status --work-path .app --output text
.\scripts\Run.ps1 tools status --work-path .app --verbose
