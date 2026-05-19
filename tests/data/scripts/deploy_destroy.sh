#!/bin/bash
# Test script for deploy_destroy lifecycle phase (destroy step)
# Corresponds to: terraform destroy

echo "=========================================="
echo "Executing DEPLOY_DESTROY step"
echo "=========================================="
echo "This step destroys the deployment resources"
echo ""
echo "Environment variables:"
echo "  STRATA_WORK_PATH: ${STRATA_WORK_PATH}"
echo "  STRATA_STAGE: ${STRATA_STAGE}"
echo "  STRATA_STEP: ${STRATA_STEP}"
echo ""
echo "Destroying resources..."
echo "  [✓] Destroying namespace.STRATA_base..."
sleep 0.5
echo "  [✓] Destroying firewall.STRATA_fw..."
sleep 0.5
echo "  [✓] Destroying virtualmachine.worker_1..."
sleep 0.5
echo "  [✓] Destroying virtualmachine.manager..."
sleep 0.5
echo ""
echo "Destroy Summary:"
echo "  Destroyed: 4 resources"
echo ""
echo "deploy_destroy completed successfully"
exit 0
