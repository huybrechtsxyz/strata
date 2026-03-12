<#
#>

# Main entry point for CLI commands
.\scripts\Run.ps1 -h

# Version command
.\scripts\Run.ps1 version # Show version with default formatting
.\scripts\Run.ps1 version --output text # Standard output with formatting
.\scripts\Run.ps1 version --output json # Output version in JSON format
.\scripts\Run.ps1 version --output raw # Error unsupported


