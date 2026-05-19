#!/usr/bin/env pwsh
# Test script for validate_before lifecycle hook
Write-Output "Executing validate_before hook..."
Write-Output "Environment variables:"
Write-Output "  STRATA_FILE: $env:STRATA_FILE"
Write-Output "  STRATA_COMMAND: $env:STRATA_COMMAND"
Write-Output "validate_before hook completed successfully"
exit 0
