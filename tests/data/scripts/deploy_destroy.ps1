#!/usr/bin/env pwsh
# Test script for deploy_destroy lifecycle phase (destroy step)
# Corresponds to: terraform destroy

Write-Output "=========================================="
Write-Output "Executing DEPLOY_DESTROY step"
Write-Output "=========================================="
Write-Output "This step destroys the deployment resources"
Write-Output ""
Write-Output "Environment variables:"
Write-Output "  XYZ_WORK_PATH: $env:XYZ_WORK_PATH"
Write-Output "  XYZ_STAGE: $env:XYZ_STAGE"
Write-Output "  XYZ_STEP: $env:XYZ_STEP"
Write-Output ""
Write-Output "Destroying resources..."
Write-Output "  [✓] Destroying namespace.xyz_base..."
Start-Sleep -Milliseconds 500
Write-Output "  [✓] Destroying firewall.xyz_fw..."
Start-Sleep -Milliseconds 500
Write-Output "  [✓] Destroying virtualmachine.worker_1..."
Start-Sleep -Milliseconds 500
Write-Output "  [✓] Destroying virtualmachine.manager..."
Start-Sleep -Milliseconds 500
Write-Output ""
Write-Output "Destroy Summary:"
Write-Output "  Destroyed: 4 resources"
Write-Output ""
Write-Output "deploy_destroy completed successfully"
exit 0
