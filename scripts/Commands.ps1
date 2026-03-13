<#
#>

# Main entry point for CLI commands
.\scripts\Run.ps1 -h

# Version command
.\scripts\Run.ps1 version # Show version with default formatting
.\scripts\Run.ps1 version --output text # Standard output with formatting
.\scripts\Run.ps1 version --output json # Output version in JSON format
.\scripts\Run.ps1 version --output raw # Error unsupported

# Help command
.\scripts\Run.ps1 help -h # Show general help
.\scripts\Run.ps1 help # Show general help
.\scripts\Run.ps1 help terraform # Show help for version command
.\scripts\Run.ps1 help tf # Show topic not found

# Temporary app directory for testing
New-Item -Path .app -ItemType Directory -Force # Create .platform directory for testing

# Session command
.\scripts\Run.ps1 session -h # Show help for session command
.\scripts\Run.ps1 session init -h # Show help for session init
.\scripts\Run.ps1 session init # Error name required
.\scripts\Run.ps1 session init --name platform --work-path .app # Initialize session with name and custom work path
