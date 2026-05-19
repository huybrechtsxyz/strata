#!/usr/bin/env pwsh
# Test script for validate_after lifecycle hook
Write-Output "Executing validate_after hook..."
Write-Output "Environment variables:"
Write-Output "  STRATA_FILE: $env:STRATA_FILE"
Write-Output "  STRATA_COMMAND: $env:STRATA_COMMAND"
Write-Output "  STRATA_VALIDATION_RESULT: $env:STRATA_VALIDATION_RESULT"
Write-Output "validate_after hook completed successfully"
exit 0
