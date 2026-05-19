#!/usr/bin/env pwsh
# Test script for deploy_setup lifecycle phase (setup step)
# Corresponds to: terraform init

Write-Output "=========================================="
Write-Output "Executing DEPLOY_SETUP step"
Write-Output "=========================================="
Write-Output "This step initializes the deployment environment"
Write-Output ""
Write-Output "Environment variables:"
Write-Output "  STRATA_WORK_PATH: $env:STRATA_WORK_PATH"
Write-Output "  STRATA_STAGE: $env:STRATA_STAGE"
Write-Output "  STRATA_STEP: $env:STRATA_STEP"
Write-Output ""
Write-Output "Actions performed:"
Write-Output "  [✓] Initialized providers"
Write-Output "  [✓] Downloaded modules"
Write-Output "  [✓] Prepared backend configuration"
Write-Output ""
Write-Output "deploy_setup completed successfully"
exit 0
