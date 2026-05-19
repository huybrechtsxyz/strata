#!/usr/bin/env pwsh
# Test script for deploy_output lifecycle phase (output step)
# Corresponds to: terraform output

Write-Output "=========================================="
Write-Output "Executing DEPLOY_OUTPUT step"
Write-Output "=========================================="
Write-Output "This step displays deployment outputs"
Write-Output ""
Write-Output "Environment variables:"
Write-Output "  STRATA_WORK_PATH: $env:STRATA_WORK_PATH"
Write-Output "  STRATA_STAGE: $env:STRATA_STAGE"
Write-Output "  STRATA_STEP: $env:STRATA_STEP"
Write-Output ""
Write-Output "Deployment Outputs:"
Write-Output "  manager_ip        = `"192.168.1.10`""
Write-Output "  worker_1_ip       = `"192.168.1.20`""
Write-Output "  firewall_id       = `"fw-xyz-001`""
Write-Output "  namespace_name    = `"STRATA_base`""
Write-Output "  deployment_region = `"eu-fr`""
Write-Output ""
Write-Output "deploy_output completed successfully"
exit 0
