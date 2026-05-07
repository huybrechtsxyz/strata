#!/usr/bin/env pwsh
# Test script for validate_after lifecycle hook
Write-Output "Executing validate_after hook..."
Write-Output "Environment variables:"
Write-Output "  XYZ_FILE: $env:XYZ_FILE"
Write-Output "  XYZ_COMMAND: $env:XYZ_COMMAND"
Write-Output "  XYZ_VALIDATION_RESULT: $env:XYZ_VALIDATION_RESULT"
Write-Output "validate_after hook completed successfully"
exit 0
