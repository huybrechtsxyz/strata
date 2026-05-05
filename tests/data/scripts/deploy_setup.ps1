#!/usr/bin/env pwsh
# Test script for deploy_setup lifecycle phase (setup step)
# Corresponds to: terraform init

Write-Output "=========================================="
Write-Output "Executing DEPLOY_SETUP step"
Write-Output "=========================================="
Write-Output "This step initializes the deployment environment"
Write-Output ""
Write-Output "Environment variables:"
Write-Output "  XYZ_WORK_PATH: $env:XYZ_WORK_PATH"
Write-Output "  XYZ_STAGE: $env:XYZ_STAGE"
Write-Output "  XYZ_STEP: $env:XYZ_STEP"
Write-Output ""
Write-Output "Actions performed:"
Write-Output "  [✓] Initialized providers"
Write-Output "  [✓] Downloaded modules"
Write-Output "  [✓] Prepared backend configuration"
Write-Output ""
Write-Output "deploy_setup completed successfully"
exit 0
