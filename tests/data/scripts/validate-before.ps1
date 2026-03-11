#!/usr/bin/env pwsh
# Test script for validate_before lifecycle hook
Write-Output "Executing validate_before hook..."
Write-Output "Environment variables:"
Write-Output "  XYZ_FILE: $env:XYZ_FILE"
Write-Output "  XYZ_COMMAND: $env:XYZ_COMMAND"
Write-Output "validate_before hook completed successfully"
exit 0
