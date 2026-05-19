#!/usr/bin/env pwsh
# Test script for deploy_apply lifecycle phase (apply step)
# Corresponds to: terraform apply

Write-Output "=========================================="
Write-Output "Executing DEPLOY_APPLY step"
Write-Output "=========================================="
Write-Output "This step applies the deployment changes"
Write-Output ""
Write-Output "Environment variables:"
Write-Output "  STRATA_WORK_PATH: $env:STRATA_WORK_PATH"
Write-Output "  STRATA_STAGE: $env:STRATA_STAGE"
Write-Output "  STRATA_STEP: $env:STRATA_STEP"
Write-Output ""
Write-Output "Applying changes..."
Write-Output "  [✓] Creating virtualmachine.manager..."
Start-Sleep -Milliseconds 500
Write-Output "  [✓] Creating virtualmachine.worker_1..."
Start-Sleep -Milliseconds 500
Write-Output "  [✓] Creating firewall.STRATA_fw..."
Start-Sleep -Milliseconds 500
Write-Output "  [✓] Modifying namespace.STRATA_base..."
Start-Sleep -Milliseconds 500
Write-Output ""
Write-Output "Apply Summary:"
Write-Output "  Created: 3 resources"
Write-Output "  Modified: 1 resource"
Write-Output "  Destroyed: 0 resources"
Write-Output ""
Write-Output "deploy_apply completed successfully"
exit 0
