#!/usr/bin/env pwsh
# Test script for deploy_check lifecycle phase (check step)
# Corresponds to: terraform validate

Write-Output "=========================================="
Write-Output "Executing DEPLOY_CHECK step"
Write-Output "=========================================="
Write-Output "This step validates the deployment configuration"
Write-Output ""
Write-Output "Environment variables:"
Write-Output "  XYZ_WORK_PATH: $env:XYZ_WORK_PATH"
Write-Output "  XYZ_STAGE: $env:XYZ_STAGE"
Write-Output "  XYZ_STEP: $env:XYZ_STEP"
Write-Output ""
Write-Output "Actions performed:"
Write-Output "  [✓] Validated syntax"
Write-Output "  [✓] Checked resource definitions"
Write-Output "  [✓] Verified variable references"
Write-Output ""
Write-Output "deploy_check completed successfully - no errors found"
exit 0
